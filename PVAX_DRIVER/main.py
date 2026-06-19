import sounddevice as sd
import numpy as np
import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports
import time
import json
import os
import threading

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ===== ESTADO GLOBAL =====
running = False
stream = None
arduino = None

# Cached by start_audio — read by audio thread, never touches tkinter
_device_index = None
_samplerate = None

# Throttle flag: prevents unbounded root.after accumulation
_pending_after = False

# Set to True by audio thread on serial error; cleared by main thread handler
_arduino_error = False

# COM port polling — tracks last known set to detect changes
_last_com_ports: set = set()

smooth_left_low = 0
smooth_left_high = 0
smooth_right_low = 0
smooth_right_high = 0

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "haptic_config.json")

DEFAULT_CONFIG = {
    "intensity": 1000,
    "smoothing": 0.45,
    "min_vibration": 30,
    "max_vibration": 255,
    "noise_gate": 5,
    "cap": 100,
    "curve": 0.7,
    "low_min_frequency": 20,
    "low_max_frequency": 300,
    "high_min_frequency": 1000,
    "high_max_frequency": 8000,
    "low_band_mode": "Média",
    "high_band_mode": "Máximo",
    "frequency_enabled": 1
}

# Snapshot of slider/checkbox values updated on every UI change.
# The audio callback reads this dict instead of calling tkinter directly,
# which makes the callback fully thread-safe.
_params = dict(DEFAULT_CONFIG)


def _update_params():
    """Snapshot current UI values into _params. Must be called from the main thread."""
    global _params
    _params = {
        "intensity":          intensity_slider.get(),
        "smoothing":          smoothing_slider.get(),
        "min_vibration":      int(min_vibration_slider.get()),
        "max_vibration":      int(max_vibration_slider.get()),
        "noise_gate":         noise_gate_slider.get(),
        "cap":                cap_slider.get(),
        "curve":              curve_slider.get(),
        "low_min_frequency":  low_min_frequency_slider.get(),
        "low_max_frequency":  low_max_frequency_slider.get(),
        "high_min_frequency": high_min_frequency_slider.get(),
        "high_max_frequency": high_max_frequency_slider.get(),
        "low_band_mode":      low_band_mode_var.get(),
        "high_band_mode":     high_band_mode_var.get(),
        "frequency_enabled":  frequency_enabled_var.get(),
    }


# ===== PROCESSAMENTO DE ÁUDIO =====

def _compute_vibration(volume, gain, curve, min_vib, max_vib, noise_gate_pct, cap_pct):
    """Pure mapping function — no tkinter, safe to call from any thread."""
    if max_vib < min_vib:
        max_vib = min_vib

    noise_gate = noise_gate_pct / 100
    cap_percent = cap_pct / 100

    scaled = min(1.0, (volume * gain) / 255)
    if scaled < noise_gate:
        return 0

    scaled = (scaled - noise_gate) / max(0.0001, (1 - noise_gate))
    scaled = scaled ** curve

    value = min_vib + scaled * (max_vib - min_vib)
    cap_value = min_vib + cap_percent * (max_vib - min_vib)
    return int(min(value, cap_value))


def map_volume_to_vibration(volume):
    p = _params
    return _compute_vibration(
        volume,
        p["intensity"], p["curve"],
        p["min_vibration"], p["max_vibration"],
        p["noise_gate"], p["cap"],
    )


def _fft_bands(channel_data, samplerate):
    """Compute FFT once per channel; reuse for both frequency bands."""
    window = np.hanning(len(channel_data))
    spectrum = np.abs(np.fft.rfft(channel_data * window))
    frequencies = np.fft.rfftfreq(len(channel_data), 1.0 / samplerate)
    norm = max(1.0, np.sum(window) / 2)
    return spectrum, frequencies, norm


def _band_energy(spectrum, frequencies, norm, min_freq, max_freq, mode):
    if max_freq <= min_freq:
        max_freq = min_freq + 1
    band = spectrum[(frequencies >= min_freq) & (frequencies <= max_freq)]
    if len(band) == 0:
        return 0.0
    energy = np.max(band) if mode == "Máximo" else np.sqrt(np.mean(band ** 2))
    return energy / norm


def audio_callback(indata, frames, time_info, status):
    global smooth_left_low, smooth_left_high, smooth_right_low, smooth_right_high
    global _pending_after, _arduino_error

    if status:
        return

    # _samplerate is set to None by stop_audio() before stream.stop() is called.
    # Checking here lets the callback exit cleanly without touching any tkinter state,
    # which prevents the deadlock that previously occurred when pressing "Parar".
    if _samplerate is None:
        return

    p = _params  # atomic reference capture — either old or new dict, never partial

    channels = indata.shape[1]
    left  = indata[:, 0]
    right = indata[:, 1] if channels >= 2 else left

    if p["frequency_enabled"] == 0:
        lv = np.sqrt(np.mean(left  ** 2))
        rv = np.sqrt(np.mean(right ** 2))
        left_low_volume = left_high_volume   = lv
        right_low_volume = right_high_volume = rv
    else:
        l_spec, l_freq, l_norm = _fft_bands(left,  _samplerate)
        r_spec, r_freq, r_norm = _fft_bands(right, _samplerate)

        lo_min, lo_max = p["low_min_frequency"],  p["low_max_frequency"]
        hi_min, hi_max = p["high_min_frequency"], p["high_max_frequency"]
        lo_mode = p["low_band_mode"]
        hi_mode = p["high_band_mode"]

        left_low_volume   = _band_energy(l_spec, l_freq, l_norm, lo_min, lo_max, lo_mode)
        left_high_volume  = _band_energy(l_spec, l_freq, l_norm, hi_min, hi_max, hi_mode)
        right_low_volume  = _band_energy(r_spec, r_freq, r_norm, lo_min, lo_max, lo_mode)
        right_high_volume = _band_energy(r_spec, r_freq, r_norm, hi_min, hi_max, hi_mode)

    alpha = p["smoothing"]
    gain  = p["intensity"]
    curve = p["curve"]
    min_v = p["min_vibration"]
    max_v = p["max_vibration"]
    ng    = p["noise_gate"]
    cap   = p["cap"]

    def vib(v):
        return _compute_vibration(v, gain, curve, min_v, max_v, ng, cap)

    smooth_left_low   = int(alpha * vib(left_low_volume)   + (1 - alpha) * smooth_left_low)
    smooth_left_high  = int(alpha * vib(left_high_volume)  + (1 - alpha) * smooth_left_high)
    smooth_right_low  = int(alpha * vib(right_low_volume)  + (1 - alpha) * smooth_right_low)
    smooth_right_high = int(alpha * vib(right_high_volume) + (1 - alpha) * smooth_right_high)

    vals = (smooth_left_low, smooth_left_high, smooth_right_low, smooth_right_high)

    # Only schedule a UI update if none is already pending.
    # This prevents the event queue from filling up faster than tkinter can drain it.
    if not _pending_after:
        _pending_after = True
        root.after(0, _flush_motor_update, *vals)

    if arduino and not _arduino_error:
        try:
            arduino.write(f"{vals[0]},{vals[1]},{vals[2]},{vals[3]}\n".encode())
        except Exception:
            # Signal main thread to clean up — don't touch tkinter/serial here
            _arduino_error = True
            root.after(0, _handle_arduino_disconnect)


def _flush_motor_update(ll, lh, rl, rh):
    """Runs on the main thread; resets the pending flag then updates the UI."""
    global _pending_after
    _pending_after = False
    update_motor_bars(ll, lh, rl, rh)


def _handle_arduino_disconnect():
    """Runs on the main thread when the audio callback detects a serial write failure."""
    global arduino, _arduino_error
    _arduino_error = False
    if arduino:
        try:
            arduino.close()
        except Exception:
            pass
    arduino = None
    status_label.config(text="Arduino desconectado (erro de comunicação)")


# ===== ATUALIZAÇÃO DA INTERFACE =====
def update_motor_bars(left_low, left_high, right_low, right_high):
    def set_bar(bar, x1, value):
        height = value * BAR_MAX_HEIGHT / 255
        motor_canvas.coords(bar, x1, BAR_BOTTOM - height, x1 + BAR_WIDTH, BAR_BOTTOM)

    set_bar(left_low_bar,   90,  left_low)
    set_bar(left_high_bar,  150, left_high)
    set_bar(right_low_bar,  390, right_low)
    set_bar(right_high_bar, 450, right_high)

    live_values_label.config(
        text=(
            f"E-Grave: {left_low:3}    "
            f"E-Agudo: {left_high:3}    "
            f"D-Grave: {right_low:3}    "
            f"D-Agudo: {right_high:3}"
        )
    )


def update_slider_labels(*args):
    intensity_value_label.config(text=f"{int(intensity_slider.get())}")
    smoothing_value_label.config(text=f"{smoothing_slider.get():.2f}")
    min_vibration_value_label.config(text=f"{int(min_vibration_slider.get())}")
    max_vibration_value_label.config(text=f"{int(max_vibration_slider.get())}")
    noise_gate_value_label.config(text=f"{int(noise_gate_slider.get())}%")
    cap_value_label.config(text=f"{int(cap_slider.get())}%")
    curve_value_label.config(text=f"{curve_slider.get():.2f}")

    low_min_frequency_value_label.config(text=f"{int(low_min_frequency_slider.get())} Hz")
    low_max_frequency_value_label.config(text=f"{int(low_max_frequency_slider.get())} Hz")
    high_min_frequency_value_label.config(text=f"{int(high_min_frequency_slider.get())} Hz")
    high_max_frequency_value_label.config(text=f"{int(high_max_frequency_slider.get())} Hz")


def on_slider_change(*args):
    update_slider_labels()
    _update_params()
    update_curve_graph()


# ===== LISTA DE DISPOSITIVOS =====
def get_input_devices():
    devices = sd.query_devices()
    result = {}
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            hostapi_name = sd.query_hostapis(d["hostapi"])["name"]
            name = f"{i} - {d['name']} ({hostapi_name})"
            result[name] = i
    return result


def get_com_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def refresh_devices():
    global device_map

    # Enumerating audio devices while a stream is open can crash the audio backend.
    # Stop the stream first, then refresh.
    was_running = running
    if was_running:
        stop_audio()

    device_map = get_input_devices()
    device_dropdown["values"] = list(device_map.keys())

    # If the previously selected device no longer exists, pick the first available one
    if audio_device.get() not in device_map:
        audio_device.set(list(device_map.keys())[0] if device_map else "")

    ports = get_com_ports()
    com_dropdown["values"] = ports

    if was_running:
        status_label.config(text="Dispositivos atualizados — reinicie o áudio")
    else:
        status_label.config(text="Lista de dispositivos atualizada")


def _poll_com_ports():
    """Runs every 2 s on the main thread to detect newly connected serial devices."""
    global _last_com_ports
    try:
        current = set(get_com_ports())
    except Exception:
        root.after(2000, _poll_com_ports)
        return

    if current != _last_com_ports:
        new_ports = current - _last_com_ports
        _last_com_ports = current
        com_dropdown["values"] = sorted(current)
        # Auto-select and notify only when a new port appears and Arduino is not connected
        if new_ports and arduino is None:
            new_port = sorted(new_ports)[0]
            com_port.set(new_port)
            status_label.config(text=f"Novo dispositivo detectado: {new_port}")

    root.after(2000, _poll_com_ports)


# ===== CONFIGURAÇÃO =====
def save_config():
    p = _params
    config = {
        "intensity":          p["intensity"],
        "smoothing":          p["smoothing"],
        "min_vibration":      p["min_vibration"],
        "max_vibration":      p["max_vibration"],
        "noise_gate":         p["noise_gate"],
        "cap":                p["cap"],
        "curve":              p["curve"],
        "low_min_frequency":  p["low_min_frequency"],
        "low_max_frequency":  p["low_max_frequency"],
        "high_min_frequency": p["high_min_frequency"],
        "high_max_frequency": p["high_max_frequency"],
        "low_band_mode":      p["low_band_mode"],
        "high_band_mode":     p["high_band_mode"],
        "frequency_enabled":  p["frequency_enabled"],
    }
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        status_label.config(text="Configuração salva")
    except OSError as e:
        status_label.config(text=f"Falha ao salvar configuração: {e}")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

        def clamp(val, lo, hi):
            return max(lo, min(hi, val))

        intensity_slider.set(         clamp(config.get("intensity",         DEFAULT_CONFIG["intensity"]),         100,  2000))
        smoothing_slider.set(         clamp(config.get("smoothing",         DEFAULT_CONFIG["smoothing"]),         0.0,  1.0))
        min_vibration_slider.set(     clamp(config.get("min_vibration",     DEFAULT_CONFIG["min_vibration"]),     0,    255))
        max_vibration_slider.set(     clamp(config.get("max_vibration",     DEFAULT_CONFIG["max_vibration"]),     0,    255))
        noise_gate_slider.set(        clamp(config.get("noise_gate",        DEFAULT_CONFIG["noise_gate"]),        0,    80))
        cap_slider.set(               clamp(config.get("cap",               DEFAULT_CONFIG["cap"]),               10,   100))
        curve_slider.set(             clamp(config.get("curve",             DEFAULT_CONFIG["curve"]),             0.3,  3.0))
        low_min_frequency_slider.set( clamp(config.get("low_min_frequency", DEFAULT_CONFIG["low_min_frequency"]), 20,   8000))
        low_max_frequency_slider.set( clamp(config.get("low_max_frequency", DEFAULT_CONFIG["low_max_frequency"]), 20,   8000))
        high_min_frequency_slider.set(clamp(config.get("high_min_frequency",DEFAULT_CONFIG["high_min_frequency"]),20,   8000))
        high_max_frequency_slider.set(clamp(config.get("high_max_frequency",DEFAULT_CONFIG["high_max_frequency"]),20,   8000))

        low_band_mode_var.set( config.get("low_band_mode",  DEFAULT_CONFIG["low_band_mode"]))
        high_band_mode_var.set(config.get("high_band_mode", DEFAULT_CONFIG["high_band_mode"]))
        frequency_enabled_var.set(config.get("frequency_enabled", DEFAULT_CONFIG["frequency_enabled"]))

        update_slider_labels()
        _update_params()
        update_curve_graph()
        status_label.config(text="Configuração carregada")

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        status_label.config(text=f"Falha ao carregar configuração: {e}")


def reset_defaults():
    intensity_slider.set(         DEFAULT_CONFIG["intensity"])
    smoothing_slider.set(         DEFAULT_CONFIG["smoothing"])
    min_vibration_slider.set(     DEFAULT_CONFIG["min_vibration"])
    max_vibration_slider.set(     DEFAULT_CONFIG["max_vibration"])
    noise_gate_slider.set(        DEFAULT_CONFIG["noise_gate"])
    cap_slider.set(               DEFAULT_CONFIG["cap"])
    curve_slider.set(             DEFAULT_CONFIG["curve"])
    low_min_frequency_slider.set( DEFAULT_CONFIG["low_min_frequency"])
    low_max_frequency_slider.set( DEFAULT_CONFIG["low_max_frequency"])
    high_min_frequency_slider.set(DEFAULT_CONFIG["high_min_frequency"])
    high_max_frequency_slider.set(DEFAULT_CONFIG["high_max_frequency"])
    low_band_mode_var.set( DEFAULT_CONFIG["low_band_mode"])
    high_band_mode_var.set(DEFAULT_CONFIG["high_band_mode"])
    frequency_enabled_var.set(DEFAULT_CONFIG["frequency_enabled"])

    update_slider_labels()
    _update_params()
    update_curve_graph()
    status_label.config(text="Padrões restaurados")


# ===== INICIAR / PARAR =====
def start_audio():
    global running, stream, _device_index, _samplerate

    if running:
        return

    selected_device = audio_device.get()
    if not selected_device:
        status_label.config(text="Selecione uma entrada de áudio primeiro")
        return

    try:
        _device_index = device_map[selected_device]
        info = sd.query_devices(_device_index)
        _samplerate = int(info["default_samplerate"])
        # Use stereo if available, fall back to mono
        channels = min(2, int(info["max_input_channels"]))

        if channels < 1:
            status_label.config(text="Dispositivo sem canais de entrada")
            return

        _update_params()

        stream = sd.InputStream(
            device=_device_index,
            channels=channels,
            samplerate=_samplerate,
            blocksize=256,
            callback=audio_callback,
            dtype="float32"
        )

        stream.start()
        running = True
        status_label.config(text=f"Escutando no dispositivo {_device_index}")

    except Exception as e:
        _samplerate = None
        status_label.config(text=f"Falha ao iniciar áudio: {e}")


def stop_audio():
    global running, stream, _samplerate

    # Signal the callback to return immediately on its next invocation.
    # This prevents the deadlock where stream.stop() waits for the callback
    # to finish while the callback would otherwise call tkinter methods.
    _samplerate = None

    try:
        if stream:
            stream.stop()
            stream.close()
            stream = None
    except Exception as e:
        status_label.config(text=f"Falha ao parar: {e}")
        return

    running = False
    status_label.config(text="Áudio parado")


# ===== SERIAL / ARDUINO =====
def connect_arduino():
    global arduino

    port = com_port.get()
    if not port:
        status_label.config(text="Selecione uma porta COM primeiro")
        return

    try:
        arduino = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)
        status_label.config(text=f"Arduino conectado: {port}")
    except Exception as e:
        status_label.config(text=f"Falha ao conectar Arduino: {e}")


def disconnect_arduino():
    global arduino

    if arduino:
        try:
            arduino.close()
        except Exception:
            pass

    arduino = None
    status_label.config(text="Arduino desconectado")


# ===== GRÁFICO =====
def update_curve_graph(*args):
    ax.clear()

    x_values = np.linspace(0, 1, 100)
    p = _params
    y_values = [
        _compute_vibration(
            x,
            p["intensity"], p["curve"],
            p["min_vibration"], p["max_vibration"],
            p["noise_gate"], p["cap"],
        )
        for x in x_values
    ]

    fig.patch.set_facecolor(CT_BG_SECONDARY)
    ax.set_facecolor(CT_BG_SECONDARY_DEEP)
    for spine in ax.spines.values():
        spine.set_edgecolor(CT_BORDER)
    ax.tick_params(colors=CT_TEXT_MUTED, which="both")
    ax.xaxis.label.set_color(CT_TEXT_MUTED)
    ax.yaxis.label.set_color(CT_TEXT_MUTED)

    ax.plot(x_values, y_values, color=CT_ACCENT, linewidth=2)
    ax.set_title("Curva de Resposta: Áudio → Vibração", color=CT_TEXT_SEC)
    ax.set_xlabel("Volume do Som")
    ax.set_ylabel("Intensidade da Vibração")
    ax.set_ylim(0, 255)
    ax.grid(True, color=CT_BORDER, linestyle="-", alpha=0.5)

    fig.subplots_adjust(left=0.11, right=0.95, top=0.92, bottom=0.13)
    canvas_graph.draw()


# ===== INTERFACE =====
root = tk.Tk()

try:
    root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"))
except Exception:
    pass

root.title("PVAX Driver (vx 0.6)")
root.geometry("760x760")
root.minsize(620, 520)


CT_BG                  = "#171717"
CT_BG_SECONDARY        = "#0E0E0E"
CT_BG_SECONDARY_ALT    = "#0A0A0A"
CT_BG_SECONDARY_RAISED = "#0f0f0f"
CT_BG_SECONDARY_DEEP   = "#1a1a1a"
CT_SPECULAR            = "#353540"
CT_BORDER              = "#1a1a1a"
CT_TEXT                = "#ffffff"
CT_TEXT_SEC            = "#c1c1c8"
CT_TEXT_MUTED          = "#A1A1A1"
CT_ACCENT              = "#2f19e7"
CT_ACCENT_HOVER        = "#c0a9ff"
CT_ACCENT_PRESS        = "#3400ff"
CT_SCROLLBAR_BG           = "#171717"
CT_SCROLLBAR_THUMB         = "#060606"
CT_SCROLLBAR_THUMB_HOVER   = "#252525"

root.configure(bg=CT_BG)

style = ttk.Style()
style.theme_use("clam")

style.configure(".",
    background=CT_BG_SECONDARY,
    foreground=CT_TEXT,
    fieldbackground=CT_BG_SECONDARY_DEEP,
    troughcolor=CT_BG_SECONDARY_DEEP,
    bordercolor=CT_BORDER,
    darkcolor=CT_BG,
    lightcolor=CT_SPECULAR,
    selectbackground=CT_ACCENT,
    selectforeground=CT_TEXT,
    font=("Helvetica", 10)
)
style.configure("TFrame",
    background=CT_BG_SECONDARY
)
style.configure("TLabel",
    background=CT_BG_SECONDARY,
    foreground=CT_TEXT,
    padding=4
)
style.configure("TLabelframe",
    background=CT_BG_SECONDARY,
    foreground=CT_TEXT,
    bordercolor=CT_BORDER,
    darkcolor=CT_BG,
    lightcolor=CT_SPECULAR,
    relief="groove",
    padding=10
)
style.configure("TLabelframe.Label",
    background=CT_BG_SECONDARY,
    foreground=CT_TEXT_MUTED,
    font=("Helvetica", 9, "bold")
)
style.configure("TButton",
    background=CT_ACCENT,
    foreground=CT_TEXT,
    bordercolor=CT_ACCENT,
    darkcolor=CT_ACCENT_PRESS,
    lightcolor=CT_ACCENT_HOVER,
    relief="flat",
    padding=(10, 6)
)
style.map("TButton",
    background=[("active", CT_ACCENT_HOVER), ("pressed", CT_ACCENT_PRESS)],
    foreground=[("active", CT_TEXT), ("pressed", CT_TEXT)],
    bordercolor=[("active", CT_ACCENT_HOVER), ("pressed", CT_ACCENT_PRESS)]
)
style.configure("TScale",
    background=CT_BG_SECONDARY,
    troughcolor=CT_BG_SECONDARY_DEEP,
    sliderlength=18,
    sliderrelief="flat",
    bordercolor=CT_BG_SECONDARY_DEEP
)
style.map("TScale",
    background=[("active", CT_BG_SECONDARY)]
)
style.configure("TCombobox",
    fieldbackground=CT_BG_SECONDARY_DEEP,
    background=CT_BG_SECONDARY_RAISED,
    foreground=CT_TEXT,
    selectbackground=CT_ACCENT,
    selectforeground=CT_TEXT,
    bordercolor=CT_BORDER,
    arrowcolor=CT_TEXT_MUTED,
    arrowsize=14
)
style.map("TCombobox",
    fieldbackground=[("readonly", CT_BG_SECONDARY_DEEP), ("focus", CT_BG_SECONDARY_DEEP)],
    foreground=[("readonly", CT_TEXT)],
    background=[("readonly", CT_BG_SECONDARY_RAISED), ("active", CT_BG_SECONDARY_ALT)],
    bordercolor=[("focus", CT_ACCENT)]
)
style.configure("TCheckbutton",
    background=CT_BG_SECONDARY,
    foreground=CT_TEXT,
    indicatorcolor=CT_BG_SECONDARY_DEEP,
    indicatorrelief="flat"
)
style.map("TCheckbutton",
    background=[("active", CT_BG_SECONDARY)],
    foreground=[("active", CT_TEXT)],
    indicatorcolor=[("selected", CT_ACCENT), ("active", CT_BG_SECONDARY_ALT)]
)
style.configure("Vertical.TScrollbar",
    background=CT_SCROLLBAR_THUMB,
    troughcolor=CT_SCROLLBAR_BG,
    bordercolor=CT_BG_SECONDARY,
    arrowcolor=CT_TEXT_MUTED,
    darkcolor=CT_BG,
    lightcolor=CT_SPECULAR,
    relief="flat"
)
style.map("Vertical.TScrollbar",
    background=[("active", CT_SCROLLBAR_THUMB_HOVER), ("disabled", CT_BG_SECONDARY)]
)

# ===== LAYOUT COM ROLAGEM =====
main_frame = ttk.Frame(root)
main_frame.pack(fill="both", expand=True)

canvas_scroll = tk.Canvas(main_frame, highlightthickness=0, bg=CT_BG)
scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas_scroll.yview)
scrollable_frame = ttk.Frame(canvas_scroll)

scrollable_frame.bind(
    "<Configure>",
    lambda e: canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
)

canvas_window = canvas_scroll.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas_scroll.configure(yscrollcommand=scrollbar.set)
canvas_scroll.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")


def resize_scroll_frame(event):
    canvas_scroll.itemconfig(canvas_window, width=event.width)

canvas_scroll.bind("<Configure>", resize_scroll_frame)


def on_mousewheel(event):
    canvas_scroll.yview_scroll(int(-1 * (event.delta / 120)), "units")

# Bind mousewheel only while the cursor is over the scroll area
canvas_scroll.bind("<Enter>", lambda e: canvas_scroll.bind_all("<MouseWheel>", on_mousewheel))
canvas_scroll.bind("<Leave>", lambda e: canvas_scroll.unbind_all("<MouseWheel>"))

# ===== CABEÇALHO =====
header = ttk.Frame(scrollable_frame)
header.pack(fill="x", padx=18, pady=(18, 8))

title_label = ttk.Label(
    header,
    text="PVAX - Pulseira Vibratória Auxiliadora da Experiência",
    font=("Helvetica", 18, "bold"),
    foreground=CT_TEXT
)
title_label.pack(anchor="w")

subtitle_label = ttk.Label(
    header,
    text="Mapeamento em tempo real de áudio estéreo e frequência para feedback por vibração",
    font=("Helvetica", 10),
    foreground=CT_TEXT_MUTED
)
subtitle_label.pack(anchor="w")

# ===== ENTRADA DE ÁUDIO =====
audio_frame = ttk.LabelFrame(scrollable_frame, text="Entrada de Áudio")
audio_frame.pack(fill="x", padx=18, pady=10)

device_map = get_input_devices()
audio_device = tk.StringVar()

device_dropdown = ttk.Combobox(
    audio_frame,
    values=list(device_map.keys()),
    textvariable=audio_device,
    width=60,
    state="readonly"
)
device_dropdown.pack(fill="x", padx=8, pady=6)

if device_map:
    audio_device.set(list(device_map.keys())[0])

button_row_audio = ttk.Frame(audio_frame)
button_row_audio.pack(fill="x", padx=8, pady=6)

ttk.Button(button_row_audio, text="Iniciar", command=start_audio).pack(side="left", padx=(0, 8))
ttk.Button(button_row_audio, text="Parar", command=stop_audio).pack(side="left", padx=(0, 8))
ttk.Button(button_row_audio, text="Atualizar Dispositivos", command=refresh_devices).pack(side="left")

# ===== SAÍDA PARA ARDUINO =====
arduino_frame = ttk.LabelFrame(scrollable_frame, text="Saída para Arduino")
arduino_frame.pack(fill="x", padx=18, pady=10)

com_port = tk.StringVar()

com_dropdown = ttk.Combobox(
    arduino_frame,
    values=get_com_ports(),
    textvariable=com_port,
    state="readonly"
)
com_dropdown.pack(fill="x", padx=8, pady=6)

button_row_arduino = ttk.Frame(arduino_frame)
button_row_arduino.pack(fill="x", padx=8, pady=6)

ttk.Button(button_row_arduino, text="Conectar Arduino", command=connect_arduino).pack(side="left", padx=(0, 8))
ttk.Button(button_row_arduino, text="Desconectar", command=disconnect_arduino).pack(side="left")

# ===== SAÍDA AO VIVO =====
live_frame = ttk.LabelFrame(scrollable_frame, text="Saída dos Motores ao Vivo")
live_frame.pack(fill="x", padx=18, pady=10)

live_values_label = ttk.Label(
    live_frame,
    text="E-Grave:   0    E-Agudo:   0    D-Grave:   0    D-Agudo:   0",
    font=("Courier", 10),
    foreground=CT_TEXT_SEC
)
live_values_label.pack(anchor="w", padx=8, pady=6)

motor_canvas = tk.Canvas(live_frame, height=260, bg=CT_BG, highlightthickness=0)
motor_canvas.pack(fill="x", padx=8, pady=8)

BAR_BOTTOM = 200
BAR_MAX_HEIGHT = 140
BAR_WIDTH = 38

left_low_bar  = motor_canvas.create_rectangle(90,  BAR_BOTTOM, 90  + BAR_WIDTH, BAR_BOTTOM, fill=CT_ACCENT, outline="")
left_high_bar = motor_canvas.create_rectangle(150, BAR_BOTTOM, 150 + BAR_WIDTH, BAR_BOTTOM, fill=CT_ACCENT, outline="")
right_low_bar  = motor_canvas.create_rectangle(390, BAR_BOTTOM, 390 + BAR_WIDTH, BAR_BOTTOM, fill=CT_ACCENT, outline="")
right_high_bar = motor_canvas.create_rectangle(450, BAR_BOTTOM, 450 + BAR_WIDTH, BAR_BOTTOM, fill=CT_ACCENT, outline="")

motor_canvas.create_text(140, 25, text="Pulseira Esquerda", font=("Helvetica", 11, "bold"), fill=CT_TEXT)
motor_canvas.create_text(440, 25, text="Pulseira Direita",  font=("Helvetica", 11, "bold"), fill=CT_TEXT)

motor_canvas.create_text(109, 225, text="Grave", fill=CT_TEXT_MUTED)
motor_canvas.create_text(169, 225, text="Agudo", fill=CT_TEXT_MUTED)
motor_canvas.create_text(409, 225, text="Grave", fill=CT_TEXT_MUTED)
motor_canvas.create_text(469, 225, text="Agudo", fill=CT_TEXT_MUTED)

# ===== AJUSTES BÁSICOS =====
basic_frame = ttk.LabelFrame(scrollable_frame, text="Ajustes Básicos")
basic_frame.pack(fill="x", padx=18, pady=10)

intensity_row = ttk.Frame(basic_frame)
intensity_row.pack(fill="x", padx=8)
ttk.Label(intensity_row, text="Intensidade").pack(side="left")
intensity_value_label = ttk.Label(intensity_row, text="1000", foreground=CT_TEXT_MUTED)
intensity_value_label.pack(side="right")
intensity_slider = ttk.Scale(basic_frame, from_=100, to=2000, orient="horizontal")
intensity_slider.set(DEFAULT_CONFIG["intensity"])
intensity_slider.pack(fill="x", padx=8, pady=(0, 8))

smoothing_row = ttk.Frame(basic_frame)
smoothing_row.pack(fill="x", padx=8)
ttk.Label(smoothing_row, text="Suavização").pack(side="left")
smoothing_value_label = ttk.Label(smoothing_row, text="0.45", foreground=CT_TEXT_MUTED)
smoothing_value_label.pack(side="right")
smoothing_slider = ttk.Scale(basic_frame, from_=0.0, to=1.0, orient="horizontal")
smoothing_slider.set(DEFAULT_CONFIG["smoothing"])
smoothing_slider.pack(fill="x", padx=8, pady=(0, 8))

# ===== AJUSTES AVANÇADOS =====
advanced_frame = ttk.LabelFrame(scrollable_frame, text="Ajustes Avançados")
advanced_frame.pack(fill="x", padx=18, pady=10)

min_vibration_row = ttk.Frame(advanced_frame)
min_vibration_row.pack(fill="x", padx=8)
ttk.Label(min_vibration_row, text="Vibração Mínima").pack(side="left")
min_vibration_value_label = ttk.Label(min_vibration_row, text="30", foreground=CT_TEXT_MUTED)
min_vibration_value_label.pack(side="right")
min_vibration_slider = ttk.Scale(advanced_frame, from_=0, to=255, orient="horizontal")
min_vibration_slider.set(DEFAULT_CONFIG["min_vibration"])
min_vibration_slider.pack(fill="x", padx=8, pady=(0, 8))

max_vibration_row = ttk.Frame(advanced_frame)
max_vibration_row.pack(fill="x", padx=8)
ttk.Label(max_vibration_row, text="Vibração Máxima").pack(side="left")
max_vibration_value_label = ttk.Label(max_vibration_row, text="255", foreground=CT_TEXT_MUTED)
max_vibration_value_label.pack(side="right")
max_vibration_slider = ttk.Scale(advanced_frame, from_=0, to=255, orient="horizontal")
max_vibration_slider.set(DEFAULT_CONFIG["max_vibration"])
max_vibration_slider.pack(fill="x", padx=8, pady=(0, 8))

noise_gate_row = ttk.Frame(advanced_frame)
noise_gate_row.pack(fill="x", padx=8)
ttk.Label(noise_gate_row, text="Cancelamento de Ruído").pack(side="left")
noise_gate_value_label = ttk.Label(noise_gate_row, text="5%", foreground=CT_TEXT_MUTED)
noise_gate_value_label.pack(side="right")
noise_gate_slider = ttk.Scale(advanced_frame, from_=0, to=80, orient="horizontal")
noise_gate_slider.set(DEFAULT_CONFIG["noise_gate"])
noise_gate_slider.pack(fill="x", padx=8, pady=(0, 8))

cap_row = ttk.Frame(advanced_frame)
cap_row.pack(fill="x", padx=8)
ttk.Label(cap_row, text="Limite Máximo").pack(side="left")
cap_value_label = ttk.Label(cap_row, text="100%", foreground=CT_TEXT_MUTED)
cap_value_label.pack(side="right")
cap_slider = ttk.Scale(advanced_frame, from_=10, to=100, orient="horizontal")
cap_slider.set(DEFAULT_CONFIG["cap"])
cap_slider.pack(fill="x", padx=8, pady=(0, 8))

curve_row = ttk.Frame(advanced_frame)
curve_row.pack(fill="x", padx=8)
ttk.Label(curve_row, text="Curva de Resposta").pack(side="left")
curve_value_label = ttk.Label(curve_row, text="0.70", foreground=CT_TEXT_MUTED)
curve_value_label.pack(side="right")
curve_slider = ttk.Scale(advanced_frame, from_=0.3, to=3.0, orient="horizontal")
curve_slider.set(DEFAULT_CONFIG["curve"])
curve_slider.pack(fill="x", padx=8, pady=(0, 8))

# ===== FAIXAS DE FREQUÊNCIA =====
frequency_frame = ttk.LabelFrame(scrollable_frame, text="Faixas de Frequência")
frequency_frame.pack(fill="x", padx=18, pady=10)

frequency_enabled_var = tk.IntVar(value=DEFAULT_CONFIG["frequency_enabled"])

frequency_checkbox = ttk.Checkbutton(
    frequency_frame,
    text="Ativar Separação por Frequência",
    variable=frequency_enabled_var
)
frequency_checkbox.pack(anchor="w", padx=8, pady=(4, 10))

low_min_frequency_row = ttk.Frame(frequency_frame)
low_min_frequency_row.pack(fill="x", padx=8)
ttk.Label(low_min_frequency_row, text="Graves - Frequência Mínima").pack(side="left")
low_min_frequency_value_label = ttk.Label(low_min_frequency_row, text="20 Hz", foreground=CT_TEXT_MUTED)
low_min_frequency_value_label.pack(side="right")
low_min_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
low_min_frequency_slider.set(DEFAULT_CONFIG["low_min_frequency"])
low_min_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

low_max_frequency_row = ttk.Frame(frequency_frame)
low_max_frequency_row.pack(fill="x", padx=8)
ttk.Label(low_max_frequency_row, text="Graves - Frequência Máxima").pack(side="left")
low_max_frequency_value_label = ttk.Label(low_max_frequency_row, text="300 Hz", foreground=CT_TEXT_MUTED)
low_max_frequency_value_label.pack(side="right")
low_max_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
low_max_frequency_slider.set(DEFAULT_CONFIG["low_max_frequency"])
low_max_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

high_min_frequency_row = ttk.Frame(frequency_frame)
high_min_frequency_row.pack(fill="x", padx=8)
ttk.Label(high_min_frequency_row, text="Agudos - Frequência Mínima").pack(side="left")
high_min_frequency_value_label = ttk.Label(high_min_frequency_row, text="1000 Hz", foreground=CT_TEXT_MUTED)
high_min_frequency_value_label.pack(side="right")
high_min_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
high_min_frequency_slider.set(DEFAULT_CONFIG["high_min_frequency"])
high_min_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

high_max_frequency_row = ttk.Frame(frequency_frame)
high_max_frequency_row.pack(fill="x", padx=8)
ttk.Label(high_max_frequency_row, text="Agudos - Frequência Máxima").pack(side="left")
high_max_frequency_value_label = ttk.Label(high_max_frequency_row, text="8000 Hz", foreground=CT_TEXT_MUTED)
high_max_frequency_value_label.pack(side="right")
high_max_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
high_max_frequency_slider.set(DEFAULT_CONFIG["high_max_frequency"])
high_max_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

# Modo de leitura das bandas
band_mode_row = ttk.Frame(frequency_frame)
band_mode_row.pack(fill="x", padx=8, pady=(6, 8))

low_band_mode_var = tk.StringVar(value=DEFAULT_CONFIG["low_band_mode"])
high_band_mode_var = tk.StringVar(value=DEFAULT_CONFIG["high_band_mode"])

low_mode_frame = ttk.Frame(band_mode_row)
low_mode_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))

ttk.Label(low_mode_frame, text="Modo dos Graves").pack(anchor="w")
low_band_mode_dropdown = ttk.Combobox(
    low_mode_frame,
    values=["Média", "Máximo"],
    textvariable=low_band_mode_var,
    state="readonly",
    width=12
)
low_band_mode_dropdown.pack(anchor="w", pady=(2, 0))

high_mode_frame = ttk.Frame(band_mode_row)
high_mode_frame.pack(side="left", fill="x", expand=True)

ttk.Label(high_mode_frame, text="Modo dos Agudos").pack(anchor="w")
high_band_mode_dropdown = ttk.Combobox(
    high_mode_frame,
    values=["Média", "Máximo"],
    textvariable=high_band_mode_var,
    state="readonly",
    width=12
)
high_band_mode_dropdown.pack(anchor="w", pady=(2, 0))

low_band_mode_dropdown.bind( "<<ComboboxSelected>>", lambda e: (_update_params(), save_config()))
high_band_mode_dropdown.bind("<<ComboboxSelected>>", lambda e: (_update_params(), save_config()))

# Keep _params in sync when the frequency checkbox is toggled
frequency_enabled_var.trace_add("write", lambda *_: _update_params())

# ===== CONFIGURAÇÃO =====
config_frame = ttk.LabelFrame(scrollable_frame, text="Configuração")
config_frame.pack(fill="x", padx=18, pady=10)

ttk.Button(config_frame, text="Salvar Configuração", command=save_config).pack(side="left", padx=8, pady=8)
ttk.Button(config_frame, text="Restaurar Padrões", command=reset_defaults).pack(side="left", padx=8, pady=8)

# ===== GRÁFICO DA CURVA =====
graph_frame = ttk.LabelFrame(scrollable_frame, text="Curva de Resposta")
graph_frame.pack(fill="both", expand=True, padx=18, pady=10)

fig = Figure(figsize=(6.4, 4.2), dpi=100, facecolor=CT_BG_SECONDARY)
ax = fig.add_subplot(111)

canvas_graph = FigureCanvasTkAgg(fig, master=graph_frame)
canvas_graph.get_tk_widget().configure(bg=CT_BG_SECONDARY, highlightthickness=0)
canvas_graph.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)

for slider in [
    intensity_slider,
    smoothing_slider,
    min_vibration_slider,
    max_vibration_slider,
    noise_gate_slider,
    cap_slider,
    curve_slider,
    low_min_frequency_slider,
    low_max_frequency_slider,
    high_min_frequency_slider,
    high_max_frequency_slider
]:
    slider.configure(command=on_slider_change)

# ===== BARRA DE STATUS =====
status_frame = ttk.Frame(scrollable_frame)
status_frame.pack(fill="x", padx=18, pady=(6, 18))

status_label = ttk.Label(
    status_frame,
    text="Não conectado",
    font=("Helvetica", 9),
    foreground=CT_TEXT_MUTED
)
status_label.pack(anchor="w")

# ===== INICIALIZAÇÃO =====
load_config()
update_slider_labels()
_update_params()
update_curve_graph()

# Seed the COM port poll baseline so we only notify about *new* ports after startup
_last_com_ports = set(get_com_ports())
root.after(2000, _poll_com_ports)

root.mainloop()

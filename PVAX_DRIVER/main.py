import sounddevice as sd
import numpy as np
import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports
import time
import json
import os

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ===== ESTADO GLOBAL =====
running = False
stream = None
arduino = None

smooth_left_low = 0
smooth_left_high = 0
smooth_right_low = 0
smooth_right_high = 0

CONFIG_FILE = "haptic_config.json"

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

# ===== PROCESSAMENTO DE ÁUDIO =====
def map_volume_to_vibration(volume):
    gain = intensity_slider.get()
    curve = curve_slider.get()

    min_vib = int(min_vibration_slider.get())
    max_vib = int(max_vibration_slider.get())

    if max_vib < min_vib:
        max_vib = min_vib

    noise_gate = noise_gate_slider.get() / 100
    cap_percent = cap_slider.get() / 100

    scaled = min(1.0, (volume * gain) / 255)

    if scaled < noise_gate:
        return 0

    scaled = (scaled - noise_gate) / max(0.0001, (1 - noise_gate))
    scaled = scaled ** curve

    value = min_vib + scaled * (max_vib - min_vib)

    cap_value = min_vib + cap_percent * (max_vib - min_vib)
    value = min(value, cap_value)

    return int(value)


def calculate_frequency_band_volume(channel_data, samplerate, min_freq, max_freq, mode):
    if max_freq <= min_freq:
        max_freq = min_freq + 1

    window = np.hanning(len(channel_data))
    windowed_data = channel_data * window

    spectrum = np.abs(np.fft.rfft(windowed_data))
    frequencies = np.fft.rfftfreq(len(windowed_data), 1 / samplerate)

    band = spectrum[(frequencies >= min_freq) & (frequencies <= max_freq)]

    if len(band) == 0:
        return 0

    if mode == "Máximo":
        band_energy = np.max(band)
    else:
        band_energy = np.sqrt(np.mean(band ** 2))

    normalized_energy = band_energy / max(1, np.sum(window) / 2)

    return normalized_energy


def audio_callback(indata, frames, time_info, status):
    global smooth_left_low, smooth_left_high, smooth_right_low, smooth_right_high

    if status:
        return

    left = indata[:, 0]
    right = indata[:, 1]

    try:
        samplerate = int(sd.query_devices(device_map[audio_device.get()])["default_samplerate"])
    except:
        return
    
    if frequency_enabled_var.get() == 0:

        left_volume = np.sqrt(np.mean(left ** 2))
        right_volume = np.sqrt(np.mean(right ** 2))

        left_low_volume = left_volume
        left_high_volume = left_volume

        right_low_volume = right_volume
        right_high_volume = right_volume
    
    else:

        low_min = low_min_frequency_slider.get()
        low_max = low_max_frequency_slider.get()
        high_min = high_min_frequency_slider.get()
        high_max = high_max_frequency_slider.get()

        low_mode = low_band_mode_var.get()
        high_mode = high_band_mode_var.get()

        left_low_volume = calculate_frequency_band_volume(left, samplerate, low_min, low_max, low_mode)
        left_high_volume = calculate_frequency_band_volume(left, samplerate, high_min, high_max, high_mode)
        right_low_volume = calculate_frequency_band_volume(right, samplerate, low_min, low_max, low_mode)
        right_high_volume = calculate_frequency_band_volume(right, samplerate, high_min, high_max, high_mode)

    alpha = smoothing_slider.get()

    left_low = map_volume_to_vibration(left_low_volume)
    left_high = map_volume_to_vibration(left_high_volume)
    right_low = map_volume_to_vibration(right_low_volume)
    right_high = map_volume_to_vibration(right_high_volume)

    smooth_left_low = int(alpha * left_low + (1 - alpha) * smooth_left_low)
    smooth_left_high = int(alpha * left_high + (1 - alpha) * smooth_left_high)
    smooth_right_low = int(alpha * right_low + (1 - alpha) * smooth_right_low)
    smooth_right_high = int(alpha * right_high + (1 - alpha) * smooth_right_high)

    root.after(
        0,
        update_motor_bars,
        smooth_left_low,
        smooth_left_high,
        smooth_right_low,
        smooth_right_high
    )

    if arduino:
        try:
            arduino.write(
                f"{smooth_left_low},{smooth_left_high},{smooth_right_low},{smooth_right_high}\n".encode()
            )
        except:
            pass

# ===== ATUALIZAÇÃO DA INTERFACE =====
def update_motor_bars(left_low, left_high, right_low, right_high):
    def set_bar(bar, x1, value):
        height = value * BAR_MAX_HEIGHT / 255
        motor_canvas.coords(bar, x1, BAR_BOTTOM - height, x1 + BAR_WIDTH, BAR_BOTTOM)

    set_bar(left_low_bar, 90, left_low)
    set_bar(left_high_bar, 150, left_high)
    set_bar(right_low_bar, 390, right_low)
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
    device_map = get_input_devices()
    device_dropdown["values"] = list(device_map.keys())

    ports = get_com_ports()
    com_dropdown["values"] = ports

    status_label.config(text="Lista de dispositivos atualizada")

# ===== CONFIGURAÇÃO =====
def save_config():
    config = {
        "intensity": intensity_slider.get(),
        "smoothing": smoothing_slider.get(),
        "min_vibration": min_vibration_slider.get(),
        "max_vibration": max_vibration_slider.get(),
        "noise_gate": noise_gate_slider.get(),
        "cap": cap_slider.get(),
        "curve": curve_slider.get(),
        "low_min_frequency": low_min_frequency_slider.get(),
        "low_max_frequency": low_max_frequency_slider.get(),
        "high_min_frequency": high_min_frequency_slider.get(),
        "high_max_frequency": high_max_frequency_slider.get(),
        "low_band_mode": low_band_mode_var.get(),
        "high_band_mode": high_band_mode_var.get(),
        "frequency_enabled": frequency_enabled_var.get()
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

    status_label.config(text="Configuração salva")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

        intensity_slider.set(config.get("intensity", DEFAULT_CONFIG["intensity"]))
        smoothing_slider.set(config.get("smoothing", DEFAULT_CONFIG["smoothing"]))
        min_vibration_slider.set(config.get("min_vibration", DEFAULT_CONFIG["min_vibration"]))
        max_vibration_slider.set(config.get("max_vibration", DEFAULT_CONFIG["max_vibration"]))
        noise_gate_slider.set(config.get("noise_gate", DEFAULT_CONFIG["noise_gate"]))
        cap_slider.set(config.get("cap", DEFAULT_CONFIG["cap"]))
        curve_slider.set(config.get("curve", DEFAULT_CONFIG["curve"]))

        low_min_frequency_slider.set(config.get("low_min_frequency", DEFAULT_CONFIG["low_min_frequency"]))
        low_max_frequency_slider.set(config.get("low_max_frequency", DEFAULT_CONFIG["low_max_frequency"]))
        high_min_frequency_slider.set(config.get("high_min_frequency", DEFAULT_CONFIG["high_min_frequency"]))
        high_max_frequency_slider.set(config.get("high_max_frequency", DEFAULT_CONFIG["high_max_frequency"]))

        low_band_mode_var.set(config.get("low_band_mode", DEFAULT_CONFIG["low_band_mode"]))
        high_band_mode_var.set(config.get("high_band_mode", DEFAULT_CONFIG["high_band_mode"]))
        frequency_enabled_var.set(
            config.get("frequency_enabled", DEFAULT_CONFIG["frequency_enabled"])
        )

        update_slider_labels()
        update_curve_graph()
        status_label.config(text="Configuração carregada")

    except:
        status_label.config(text="Falha ao carregar configuração")


def reset_defaults():
    intensity_slider.set(DEFAULT_CONFIG["intensity"])
    smoothing_slider.set(DEFAULT_CONFIG["smoothing"])
    min_vibration_slider.set(DEFAULT_CONFIG["min_vibration"])
    max_vibration_slider.set(DEFAULT_CONFIG["max_vibration"])
    noise_gate_slider.set(DEFAULT_CONFIG["noise_gate"])
    cap_slider.set(DEFAULT_CONFIG["cap"])
    curve_slider.set(DEFAULT_CONFIG["curve"])

    low_min_frequency_slider.set(DEFAULT_CONFIG["low_min_frequency"])
    low_max_frequency_slider.set(DEFAULT_CONFIG["low_max_frequency"])
    high_min_frequency_slider.set(DEFAULT_CONFIG["high_min_frequency"])
    high_max_frequency_slider.set(DEFAULT_CONFIG["high_max_frequency"])

    low_band_mode_var.set(DEFAULT_CONFIG["low_band_mode"])
    high_band_mode_var.set(DEFAULT_CONFIG["high_band_mode"])
    frequency_enabled_var.set(DEFAULT_CONFIG["frequency_enabled"])

    update_slider_labels()
    update_curve_graph()
    status_label.config(text="Padrões restaurados")

# ===== INICIAR / PARAR =====
def start_audio():
    global running, stream

    if running:
        return

    selected_device = audio_device.get()
    if not selected_device:
        status_label.config(text="Selecione uma entrada de áudio primeiro")
        return

    try:
        device_index = device_map[selected_device]
        info = sd.query_devices(device_index)
        samplerate = int(info["default_samplerate"])

        stream = sd.InputStream(
            device=device_index,
            channels=2,
            samplerate=samplerate,
            blocksize=256,
            callback=audio_callback,
            dtype="float32"
        )

        stream.start()
        running = True
        status_label.config(text=f"Escutando no dispositivo {device_index}")

    except Exception as e:
        status_label.config(text=f"Falha ao iniciar áudio: {e}")


def stop_audio():
    global running, stream

    try:
        if stream:
            stream.stop()
            stream.close()
            stream = None

        running = False
        status_label.config(text="Áudio parado")

    except Exception as e:
        status_label.config(text=f"Falha ao parar: {e}")

# ===== SERIAL / ARDUINO =====
def connect_arduino():
    global arduino

    port = com_port.get()
    if not port:
        status_label.config(text="Selecione uma porta COM primeiro")
        return

    try:
        arduino = serial.Serial(port, 115200)
        time.sleep(2)
        status_label.config(text=f"Arduino conectado: {port}")
    except Exception as e:
        status_label.config(text=f"Falha ao conectar Arduino: {e}")


def disconnect_arduino():
    global arduino

    if arduino:
        try:
            arduino.close()
        except:
            pass

    arduino = None
    status_label.config(text="Arduino desconectado")

# ===== GRÁFICO =====
def update_curve_graph(*args):
    ax.clear()

    x_values = np.linspace(0, 1, 100)
    y_values = []

    gain = intensity_slider.get()
    curve = curve_slider.get()

    min_vib = int(min_vibration_slider.get())
    max_vib = int(max_vibration_slider.get())

    if max_vib < min_vib:
        max_vib = min_vib

    noise_gate = noise_gate_slider.get() / 100
    cap_percent = cap_slider.get() / 100

    for x in x_values:
        scaled = min(1.0, (x * gain) / 255)

        if scaled < noise_gate:
            y = 0
        else:
            scaled = (scaled - noise_gate) / max(0.0001, (1 - noise_gate))
            scaled = scaled ** curve

            y = min_vib + scaled * (max_vib - min_vib)

            cap_value = min_vib + cap_percent * (max_vib - min_vib)
            y = min(y, cap_value)

        y_values.append(y)

    ax.plot(x_values, y_values)
    ax.set_title("Curva de Resposta: Áudio → Vibração")
    ax.set_xlabel("Volume do Som")
    ax.set_ylabel("Intensidade da Vibração")
    ax.set_ylim(0, 255)
    ax.grid(True)

    fig.subplots_adjust(left=0.11, right=0.95, top=0.92, bottom=0.13)
    canvas_graph.draw()

# ===== INTERFACE =====
root = tk.Tk()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    root.iconbitmap("icon.ico")
except:
    pass

root.title("PVAX Driver (v0.3)")
root.geometry("760x760")
root.minsize(620, 520)

style = ttk.Style()
style.theme_use("clam")
style.configure("TButton", padding=6)
style.configure("TLabel", padding=4)
style.configure("TLabelframe", padding=10)

# ===== LAYOUT COM ROLAGEM =====
main_frame = ttk.Frame(root)
main_frame.pack(fill="both", expand=True)

canvas_scroll = tk.Canvas(main_frame, highlightthickness=0)
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

canvas_scroll.bind_all("<MouseWheel>", on_mousewheel)

# ===== CABEÇALHO =====
header = ttk.Frame(scrollable_frame)
header.pack(fill="x", padx=18, pady=(18, 8))

title_label = ttk.Label(header, text="PVAX - Pulseira Vibratória Auxiliadora da Experiência", font=("Segoe UI", 18, "bold"))
title_label.pack(anchor="w")

subtitle_label = ttk.Label(
    header,
    text="Mapeamento em tempo real de áudio estéreo e frequência para feedback por vibração",
    font=("Segoe UI", 10)
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
    font=("Consolas", 10)
)
live_values_label.pack(anchor="w", padx=8, pady=6)

motor_canvas = tk.Canvas(live_frame, height=260, bg="#f0f0f0", highlightthickness=0)
motor_canvas.pack(fill="x", padx=8, pady=8)

BAR_BOTTOM = 200
BAR_MAX_HEIGHT = 140
BAR_WIDTH = 38

left_low_bar = motor_canvas.create_rectangle(90, BAR_BOTTOM, 90 + BAR_WIDTH, BAR_BOTTOM, fill="#444444")
left_high_bar = motor_canvas.create_rectangle(150, BAR_BOTTOM, 150 + BAR_WIDTH, BAR_BOTTOM, fill="#444444")
right_low_bar = motor_canvas.create_rectangle(390, BAR_BOTTOM, 390 + BAR_WIDTH, BAR_BOTTOM, fill="#444444")
right_high_bar = motor_canvas.create_rectangle(450, BAR_BOTTOM, 450 + BAR_WIDTH, BAR_BOTTOM, fill="#444444")

motor_canvas.create_text(140, 25, text="Pulseira Esquerda", font=("Segoe UI", 11, "bold"))
motor_canvas.create_text(440, 25, text="Pulseira Direita", font=("Segoe UI", 11, "bold"))

motor_canvas.create_text(109, 225, text="Grave")
motor_canvas.create_text(169, 225, text="Agudo")
motor_canvas.create_text(409, 225, text="Grave")
motor_canvas.create_text(469, 225, text="Agudo")

# ===== AJUSTES BÁSICOS =====
basic_frame = ttk.LabelFrame(scrollable_frame, text="Ajustes Básicos")
basic_frame.pack(fill="x", padx=18, pady=10)

intensity_row = ttk.Frame(basic_frame)
intensity_row.pack(fill="x", padx=8)
ttk.Label(intensity_row, text="Intensidade").pack(side="left")
intensity_value_label = ttk.Label(intensity_row, text="1000")
intensity_value_label.pack(side="right")
intensity_slider = ttk.Scale(basic_frame, from_=100, to=2000, orient="horizontal")
intensity_slider.set(DEFAULT_CONFIG["intensity"])
intensity_slider.pack(fill="x", padx=8, pady=(0, 8))

smoothing_row = ttk.Frame(basic_frame)
smoothing_row.pack(fill="x", padx=8)
ttk.Label(smoothing_row, text="Suavização").pack(side="left")
smoothing_value_label = ttk.Label(smoothing_row, text="0.45")
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
min_vibration_value_label = ttk.Label(min_vibration_row, text="30")
min_vibration_value_label.pack(side="right")
min_vibration_slider = ttk.Scale(advanced_frame, from_=0, to=255, orient="horizontal")
min_vibration_slider.set(DEFAULT_CONFIG["min_vibration"])
min_vibration_slider.pack(fill="x", padx=8, pady=(0, 8))

max_vibration_row = ttk.Frame(advanced_frame)
max_vibration_row.pack(fill="x", padx=8)
ttk.Label(max_vibration_row, text="Vibração Máxima").pack(side="left")
max_vibration_value_label = ttk.Label(max_vibration_row, text="255")
max_vibration_value_label.pack(side="right")
max_vibration_slider = ttk.Scale(advanced_frame, from_=0, to=255, orient="horizontal")
max_vibration_slider.set(DEFAULT_CONFIG["max_vibration"])
max_vibration_slider.pack(fill="x", padx=8, pady=(0, 8))

noise_gate_row = ttk.Frame(advanced_frame)
noise_gate_row.pack(fill="x", padx=8)
ttk.Label(noise_gate_row, text="Cancelamento de Ruído").pack(side="left")
noise_gate_value_label = ttk.Label(noise_gate_row, text="5%")
noise_gate_value_label.pack(side="right")
noise_gate_slider = ttk.Scale(advanced_frame, from_=0, to=80, orient="horizontal")
noise_gate_slider.set(DEFAULT_CONFIG["noise_gate"])
noise_gate_slider.pack(fill="x", padx=8, pady=(0, 8))

cap_row = ttk.Frame(advanced_frame)
cap_row.pack(fill="x", padx=8)
ttk.Label(cap_row, text="Limite Máximo").pack(side="left")
cap_value_label = ttk.Label(cap_row, text="100%")
cap_value_label.pack(side="right")
cap_slider = ttk.Scale(advanced_frame, from_=10, to=100, orient="horizontal")
cap_slider.set(DEFAULT_CONFIG["cap"])
cap_slider.pack(fill="x", padx=8, pady=(0, 8))

curve_row = ttk.Frame(advanced_frame)
curve_row.pack(fill="x", padx=8)
ttk.Label(curve_row, text="Curva de Resposta").pack(side="left")
curve_value_label = ttk.Label(curve_row, text="0.70")
curve_value_label.pack(side="right")
curve_slider = ttk.Scale(advanced_frame, from_=0.3, to=3.0, orient="horizontal")
curve_slider.set(DEFAULT_CONFIG["curve"])
curve_slider.pack(fill="x", padx=8, pady=(0, 8))

# ===== FAIXAS DE FREQUÊNCIA =====
frequency_frame = ttk.LabelFrame(scrollable_frame, text="Faixas de Frequência")
frequency_frame.pack(fill="x", padx=18, pady=10)

frequency_enabled_var = tk.IntVar(
    value=DEFAULT_CONFIG["frequency_enabled"]
)

frequency_checkbox = ttk.Checkbutton(
    frequency_frame,
    text="Ativar Separação por Frequência",
    variable=frequency_enabled_var
)
frequency_checkbox.pack(anchor="w", padx=8, pady=(4, 10))

low_min_frequency_row = ttk.Frame(frequency_frame)
low_min_frequency_row.pack(fill="x", padx=8)
ttk.Label(low_min_frequency_row, text="Graves - Frequência Mínima").pack(side="left")
low_min_frequency_value_label = ttk.Label(low_min_frequency_row, text="20 Hz")
low_min_frequency_value_label.pack(side="right")
low_min_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
low_min_frequency_slider.set(DEFAULT_CONFIG["low_min_frequency"])
low_min_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

low_max_frequency_row = ttk.Frame(frequency_frame)
low_max_frequency_row.pack(fill="x", padx=8)
ttk.Label(low_max_frequency_row, text="Graves - Frequência Máxima").pack(side="left")
low_max_frequency_value_label = ttk.Label(low_max_frequency_row, text="300 Hz")
low_max_frequency_value_label.pack(side="right")
low_max_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
low_max_frequency_slider.set(DEFAULT_CONFIG["low_max_frequency"])
low_max_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

high_min_frequency_row = ttk.Frame(frequency_frame)
high_min_frequency_row.pack(fill="x", padx=8)
ttk.Label(high_min_frequency_row, text="Agudos - Frequência Mínima").pack(side="left")
high_min_frequency_value_label = ttk.Label(high_min_frequency_row, text="1000 Hz")
high_min_frequency_value_label.pack(side="right")
high_min_frequency_slider = ttk.Scale(frequency_frame, from_=20, to=8000, orient="horizontal")
high_min_frequency_slider.set(DEFAULT_CONFIG["high_min_frequency"])
high_min_frequency_slider.pack(fill="x", padx=8, pady=(0, 8))

high_max_frequency_row = ttk.Frame(frequency_frame)
high_max_frequency_row.pack(fill="x", padx=8)
ttk.Label(high_max_frequency_row, text="Agudos - Frequência Máxima").pack(side="left")
high_max_frequency_value_label = ttk.Label(high_max_frequency_row, text="8000 Hz")
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

low_band_mode_dropdown.bind("<<ComboboxSelected>>", lambda event: save_config())
high_band_mode_dropdown.bind("<<ComboboxSelected>>", lambda event: save_config())

# ===== CONFIGURAÇÃO =====
config_frame = ttk.LabelFrame(scrollable_frame, text="Configuração")
config_frame.pack(fill="x", padx=18, pady=10)

ttk.Button(config_frame, text="Salvar Configuração", command=save_config).pack(side="left", padx=8, pady=8)
ttk.Button(config_frame, text="Restaurar Padrões", command=reset_defaults).pack(side="left", padx=8, pady=8)

# ===== GRÁFICO DA CURVA =====
graph_frame = ttk.LabelFrame(scrollable_frame, text="Curva de Resposta")
graph_frame.pack(fill="both", expand=True, padx=18, pady=10)

fig = Figure(figsize=(6.4, 4.2), dpi=100)
ax = fig.add_subplot(111)

canvas_graph = FigureCanvasTkAgg(fig, master=graph_frame)
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

status_label = ttk.Label(status_frame, text="Não conectado", font=("Segoe UI", 9))
status_label.pack(anchor="w")

load_config()
update_slider_labels()
update_curve_graph()

root.mainloop()

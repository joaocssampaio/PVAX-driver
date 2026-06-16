# 🎵 PVAX - *Pulseira Vibratória Auxiliadora da Experiência*

> **Transforme seu áudio em experiências vibratórias imersivas**

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Status](https://img.shields.io/badge/Status-Ativo-brightgreen.svg)
![License](https://img.shields.io/badge/License-MIT-orange.svg)

---

## 📋 Índice

- [🎯 Sobre o Projeto](#-sobre-o-projeto)
- [⚙️ Requisitos do Sistema](#-requisitos-do-sistema)
- [🚀 Instalação](#-instalação)
- [▶️ Como Executar](#-como-executar)
- [🔊 Configuração de Áudio](#-configuração-de-áudio)
- [🎮 Como Usar](#-como-usar)
- [🐛 Solução de Problemas](#-solução-de-problemas)
- [📞 Suporte](#-suporte)

---

## 🎯 Sobre o Projeto

O **PVAX** é uma aplicação inovadora que converte sinais de áudio em padrões de vibração, criando uma experiência sensorial única. Perfeito para:

- ✨ Experiências imersivas de áudio
- 🎮 Aplicações de realidade aumentada
- 🎵 Sincronização com música
- 🎬 Efeitos especiais em tempo real

---

## ⚙️ Requisitos do Sistema

Antes de começar, certifique-se de que seu sistema atende aos seguintes requisitos:

| Requisito | Mínimo | Recomendado |
|-----------|--------|-------------|
| **Python** | 3.10 | 3.11+ |
| **RAM** | 4 GB | 8 GB |
| **Processador** | Dual Core | Quad Core ou superior |
| **SO** | Windows 10+ | Windows 11 |

### Dependências Opcionais:
- 🔌 VB-Cable (para roteamento avançado de áudio) - [Baixar aqui](https://vb-audio.com/Cable/)

---

## 🚀 Instalação

### **Passo 1: Verificar a Versão do Python**

Abra o **Prompt de Comando** ou **PowerShell** e execute:

```bash
python --version
```

Certifique-se de que a versão é **3.10 ou superior**. Se não tiver Python instalado, [baixe aqui](https://www.python.org/downloads/).

### **Passo 2: Executar o Instalador**

1. Instale o Python 3.10 ou superior.
2. Navegue até a pasta do projeto
3. Clique duas vezes em **`setup.bat`**
4. Aguarde a instalação das dependências

> **⏱️ Nota:** Este processo pode levar alguns minutos dependendo da sua conexão com a internet.

### **Passo 3: Verificar a Instalação**

Após a conclusão, uma janela de confirmação deve aparecer. Se houver erros, verifique a seção [🐛 Solução de Problemas](#-solução-de-problemas).

---

## ▶️ Como Executar

Você tem duas opções para executar o PVAX:

### **Opção 1: Executar via Python** (Recomendado para Desenvolvimento)

Execute o arquivo "main.py":

```bash
python main.py
```

### **Opção 2: Executar o Executável** (Mais Rápido)

Execute o arquivo "main.exe" (caso disponível) clicando duas vezes nele.

> **💡 Dica:** Se você está desenvolvendo, use a Opção 1. Para distribuição, use a Opção 2.

---

## 🔊 Configuração de Áudio

### ⚠️ Problema: Nenhum Áudio é Detectado?

Se nenhum áudio for detectado, instale o VB-Cable para roteamento avançado de áudio:

### **Passo 1: Instalar o VB-Cable**

1. Acesse: https://vb-audio.com/Cable/
2. Clique em **Download**
3. Instale e **reinicie seu computador**

### **Passo 2: Configurar a Saída de Áudio do Sistema**

1. Clique com botão direito no **ícone de volume** (canto inferior direito)
2. Selecione **Sons** (ou acesse **Configurações > Som**)
3. Em **Dispositivos de saída**, procure por **"CABLE Input"**
4. Selecione-o como padrão
5. Defina a saída de áudio do sistema como **"CABLE Input"**

### **Passo 3: Configurar o PVAX**

1. Abra o aplicativo PVAX
2. Nas **Configurações de Áudio**, selecione **"CABLE Output"** no aplicativo
3. Clique em **Conectar**

### **Diagrama Visual do Fluxo de Áudio**

```
┌─────────────────────────────────┐
│   Fonte de Áudio                │
│   (Spotify, YouTube, etc.)      │
└────────────┬────────────────────┘
             │
             ▼
    ┌────────────────────┐
    │   CABLE Input      │
    │ (Saída do Sistema) │
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │  PVAX Application  │
    │ CABLE Output       │
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │  Pulseira Vibratória│
    │  (Saída Vibratória)│
    └────────────────────┘
```

---

## 🎮 Como Usar

### **Controles Básicos**

1. **Iniciar**: Clique no botão **Conectar**
2. **Parar**: Clique no botão **Desconectar**
3. **Sensibilidade**: Ajuste o controle deslizante para aumentar/diminuir a intensidade das vibrações
4. **Frequência**: Personalize os padrões de vibração conforme desejar

### **Modo Avançado**

- 📊 **Visualizador de Espectro**: Acompanhe o espectro de áudio em tempo real
- ⚙️ **Perfis**: Salve suas configurações favoritas
- 🎚️ **Equalizador**: Ajuste faixas de frequência específicas

---

## 🐛 Solução de Problemas

### **❌ Erro: "Python não reconhecido"**

**Solução:** Adicione Python ao PATH do Windows:
1. Abra **Variáveis de Ambiente**
2. Encontre `Path` em Variáveis de Sistema
3. Clique **Editar** e adicione o caminho de instalação do Python (ex: `C:\Users\SeuUsuário\AppData\Local\Programs\Python\Python310`)
4. Reinicie o terminal

### **❌ Erro: "Módulos não encontrados"**

**Solução:** Execute manualmente:
```bash
pip install -r requirements.txt
```

### **❌ Nenhuma Vibração Detectada**

**Solução:**
- ✅ Verifique se o CABLE está instalado corretamente
- ✅ Teste a saída de áudio em **Configurações > Som**
- ✅ Certifique-se de que o volume não está no mínimo
- ✅ Reinicie a aplicação

### **❌ A Aplicação Trava**

**Solução:**
- ✅ Atualize seus drivers de áudio
- ✅ Feche outras aplicações que usam áudio
- ✅ Reinicie o computador

---

## 📊 Estrutura do Projeto

```
PVAX-driver/
├── main.py              # Arquivo principal
├── main.exe             # Executável compilado
├── setup.bat            # Script de instalação
├── requirements.txt     # Dependências Python
├── config/              # Arquivos de configuração
├── src/                 # Código-fonte
└── docs/                # Documentação adicional
```

---

## 🎓 Próximos Passos

- 📖 Leia a [documentação completa](docs/)
- 🔧 Configure seus [perfis personalizados](docs/profiles.md)
- 💬 Junte-se à nossa [comunidade](https://github.com/miguel-drechsler/PVAX-driver/discussions)

---

## 📞 Suporte

Encontrou um problema? 

- 🐛 [Reporte um bug](https://github.com/miguel-drechsler/PVAX-driver/issues)
- 💡 [Sugira uma melhoria](https://github.com/miguel-drechsler/PVAX-driver/issues/new)
- 💬 [Pergunte na comunidade](https://github.com/miguel-drechsler/PVAX-driver/discussions)

---

## 📄 Licença

Este projeto está sob a licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.

---

## ✨ Contribuições

Contribuições são bem-vindas! Por favor, abra um **Pull Request** com suas melhorias.

---


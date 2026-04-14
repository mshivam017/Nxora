# NxoraAI User Manual

Welcome to **NxoraAI** (formerly Jarvis)—your advanced neural desktop assistant. NxoraAI integrates Deep Learning Large Language Models (LLMs) natively with your Windows Operating System, giving you unprecedented control over your computer via natural language and voice.

This manual will guide you through NxoraAI's interface, capabilities, and the full range of voice/text commands.

---

## Table of Contents
1. [Interface Overview](#1-interface-overview)
2. [Voice Activation & Chat](#2-voice-activation--chat)
3. [System Setup & Configuration](#3-system-setup--configuration)
4. [Master Command List](#4-master-command-list)
   - [Core Intelligence & Conversation](#core-intelligence--conversation)
   - [Windows OS Control](#windows-os-control)
   - [File & Code Generation](#file--code-generation)
   - [Web Automation & Vision](#web-automation--vision)
   - [Developer Utilities](#developer-utilities)
5. [Advanced Features](#5-advanced-features)

---

## 1. Interface Overview

NxoraAI features a highly polished "Galaxy/Developer" aesthetic built on PyQtWebEngine.

- **Main Chat Window**: This is where you speak to NxoraAI. The AI's responses will appear as chat bubbles. The UI fully supports rendering syntax-highlighted code blocks (Python, HTML, JS) for developer queries.
- **The Energy Core (Status Indicator)**: The animated rings in the center of the application represent NxoraAI's current state:
  - **Blue/Green Rings (Standby)**: NxoraAI is idle and waiting for commands.
  - **Pulsing Glow (Listening)**: NxoraAI is actively listening to your microphone.
  - **Orange Fast-Spinning Rings (Processing)**: NxoraAI is generating a response using its neural network.
- **Theme Toggle**: In the right sidebar under "Quick Shortcuts", you can toggle between the sleek Dark Mode and a pristine, Apple-style Light Mode.
- **Input Controls**: You can interact using the text input box at the bottom, or click the Microphone icon to initiate voice listening.

---

## 2. Voice Activation & Chat

NxoraAI runs completely offline Wake Word detection. 

1. **Summoning**: Simply say `"Nxora"` or `"Jarvis"` out loud. The center core will pulse to indicate she is listening.
2. **Speaking**: Give your command. NxoraAI uses Google Speech Recognition to parse your intent.
3. **Execution**: The AI will process the command, speak the result aloud natively using `pyttsx3`, and print the response to the chat feed.
4. **Text Fallback**: If you prefer not to speak, use the text box at the bottom of the window to send silent commands.

---

## 3. System Setup & Configuration

NxoraAI is highly customizable via the `config.json` file in the root directory.

```json
{
  "assistant_name": "Nxora",
  "wake_word": "Next-Sora",
  "theme": "dark",
  "language": "en-US"
}
```
If you change the `"assistant_name"` here, the entire user interface, window title, and internal database naming will inherently update on restart to reflect your chosen name.

### API Keys (`.env`)
Certain advanced web reasoning capabilities rely on external APIs. Ensure you have a `.env` file in your root directory containing:
```
GEMINI_API_KEY=your_key_here
```

---

## 4. Master Command List

NxoraAI parses your natural language, meaning you don't have to say these **exactly** as written. The neural intent router will understand variations.

### Core Intelligence & Conversation
NxoraAI runs the **NxoraAI Tiered Model Engine** (`model_engine.py`) which auto-selects the best available CPU model based on your system RAM: Phi-3-mini (GGUF) -> Qwen2.5-0.5B (GGUF) -> SmolLM2-135M (GGUF) -> SmolLM-135M (HuggingFace fp32) -> Gemini API cloud fallback.
* `"Who built you?"` -> Explains her origins.
* `"Tell me a joke"` -> Generates a context-aware joke.
* `"What time is it?"` -> Gives you the current local time.
* `"What is the weather today?"` -> Fetches the live local weather via web API.
* `"My favorite song is..."` -> Saves a permanent memory to the SQLite database.
* `"Play my favorite song"` -> Pulls the memory from the DB and plays it.
* **Robust Error Handling**: All internal systems, including database migrations and neural text generation pipelines, are safely encapsulated with custom `@safe_call` handlers. In the event of network failure or database corruption, NxoraAI will not crash. It actively bridges traceback logs up to a visual Javascript toast alert seamlessly inside your graphical interface.

### Windows OS Control
* `"Mute"` / `"Volume Up"` / `"Volume Down"` -> Physically controls Windows system audio via simulated keypresses.
* `"Shutdown PC"` / `"Restart PC"` / `"Lock PC"` -> Executes native Windows power-state commands.
* `"Put the PC to sleep"` -> Uses Win32 ctypes to securely sleep the operating system.
* `"Empty the trash"` / `"Clear recycle bin"` -> Uses Windows API to permanently empty the system recycle bin without confirmation dialogues.
* `"Clean my PC"` / `"Clear temp files"` -> Safely loops through your `%TEMP%` directory and deletes temporary caches to free up storage space.
* `"Check storage"` / `"How much storage"` -> Uses `psutil` to report the total free gigabytes and percentage used on your main operational drive.
* `"Set a timer for [X] minutes"` -> Spawns a background thread that will speak and pop up a native Windows Alert Box when the time expires.
* `"Take a note [content]"` -> Appends the dictated string directly into a persistent `Nxora_Notes.txt` document on your Desktop with a timestamp.
* `"New tab"` / `"Close tab"` / `"Switch tab"` -> Executes physical `Ctrl+T`, `Ctrl+W`, and `Ctrl+Tab` shortcuts to manage your actively focused web browser.
* `"Open task manager"` -> Instantly triggers the `Ctrl+Shift+Esc` hotkey to summon Task Manager.
* `"Open clipboard history"` -> Triggers the `Win+V` Windows clipboard UI panel.
* `"Open Bluetooth settings"` / `"Open Windows settings"` -> Launches the native MS-Settings application URIs directly.
* `"Open Snipping Tool"` -> Launches Windows Snipping Tool overlay.
* `"Mute my microphone"` -> Opens the privacy settings for the microphone.
* `"Take a screenshot"` -> Silently captures your desktop and saves the `.png` image locally.
* `"Open [App Name]"` -> E.g., *"Open Calculator"* or *"Open command prompt"*. NexorAI will use shell mapping to launch the local Windows application.

### File & Code Generation
NxoraAI features a powerful conversational flow for generating logic and outputting files directly to your hard drive.

* **Targeted Scripting**: `"Write a modern HTML website about [Topic]"`. 
  * NxoraAI hooks into the neural engine, natively generates valid syntax-free HTML, and launches the code in your default workspace.
* **Multi-Turn File Creation**: `"Create a file"` or `"Write some code"`. 
  * NxoraAI will enter a guided state machine. She will ask you what the topic is, what to name the file, wait for your confirmation to save it, and even ask if you want it opened immediately.

### Web Automation & Vision
* `"Play [Song] on YouTube"` -> Launches your browser and auto-plays the requested video.
* `"Search Google for [Topic]"` -> Seamless web queries.
* `"What does it say on my screen?"` (Computer Vision) -> Takes a silent screenshot, runs Tesseract OCR across the image, and feeds the raw text back to the LLM to answer what you are looking at.
* `"Summarize my clipboard"` -> Reads your actively copied text directly from memory and analyzes it.

### Developer Utilities
* `"Test my internet speed"` -> Uses `speedtest-cli` to benchmark live ping, download, and upload speeds.
* `"Run diagnostic"` / `"System health"` -> Uses `psutil` to report your PC's active CPU and Memory (RAM) percentage load.
* `"Battery status"` -> Reports laptop percentage and plugged-in state.
* `"What is my IP address?"` -> Fetches external public network routing IP.
* `"Kill all [App Name] processes"` -> E.g., *"Kill all Chrome processes"*. Forcibly terminates frozen applications.
* `"Commit my code with message 'Updates'"` -> Executes raw `git add .` and `git commit -m` flows in the local workspace directory.

---

## 5. Advanced Features

### Mobile Network API
NxoraAI is structurally pre-configured to host a local REST API (`api/api.py`). This allows you to launch the Python backend and connect a standard React Native mobile client to talk to NxoraAI from your phone over your local WiFi network.

### SQLite History Logs
Every conversation you have is logged indefinitely in `data/Nxora_memory.db`. If you need to retrieve a piece of code NxoraAI generated yesterday, you can access the database directly.

Enjoy controlling your ecosystem with NxoraAI!

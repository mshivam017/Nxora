import sys
import os

# Python 3.8+ Windows DLL workaround for PyQt5
if sys.platform == 'win32':
    qt_bin = os.path.join(sys.prefix, "Lib", "site-packages", "PyQt5", "Qt5", "bin")
    if os.path.exists(qt_bin):
        os.add_dll_directory(qt_bin)

import json
import threading
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QUrl, pyqtSlot, QObject, QThread, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel

from core.database import MemoryDB
from core.engine import AIWorker, safe_call
from core.audio import WakeWordWorker, VoiceListenerThread, speak
class VitalsWorker(QThread):
    vitals_ready = pyqtSignal(dict)

    def __init__(self, interval=4):
        super().__init__()
        self.interval = interval
        self.running = True

    def run(self):
        import psutil
        import time
        from datetime import datetime

        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
        except Exception:
            boot_time = datetime.now()

        while self.running:
            try:
                cpu = psutil.cpu_percent()
                ram = psutil.virtual_memory()
                ram_pct = ram.percent
                ram_used = round(ram.used / (1024 ** 3), 1)
                ram_total = round(ram.total / (1024 ** 3), 1)

                battery = psutil.sensors_battery()
                bat_pct = battery.percent if battery else 100
                bat_plugged = battery.power_plugged if battery else True

                uptime_delta = datetime.now() - boot_time
                hours, remainder = divmod(int(uptime_delta.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    uptime_str = f"{hours}h {minutes}m"
                else:
                    uptime_str = f"{minutes}m"

                apps = []
                seen_names = set()
                for proc in psutil.process_iter(['name', 'memory_percent']):
                    try:
                        pname = proc.info['name']
                        if not pname or pname.lower() in [
                            'system idle process', 'system', 'registry', 'smss.exe', 'csrss.exe', 
                            'wininit.exe', 'services.exe', 'lsass.exe', 'svchost.exe', 'fontdrvhost.exe', 
                            'dwm.exe', 'spoolsv.exe', 'explorer.exe', 'taskhostw.exe', 'runtimebroker.exe',
                            'searchindexer.exe', 'ctfmon.exe', 'securityhealthservice.exe', 'conhost.exe'
                        ]:
                            continue
                        
                        clean_name = pname.split('.exe')[0].title()
                        if clean_name in seen_names:
                            continue
                            
                        mem = proc.info['memory_percent'] or 0
                        apps.append((clean_name, mem, pname.lower()))
                        seen_names.add(clean_name)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass

                apps.sort(key=lambda x: x[1], reverse=True)
                top_apps = [{"name": a[0], "raw_name": a[2]} for a in apps[:4]]

                data = {
                    "cpu": cpu,
                    "ram_pct": ram_pct,
                    "ram_used": f"{ram_used} GB",
                    "ram_total": f"{ram_total} GB",
                    "battery_pct": bat_pct,
                    "battery_plugged": bat_plugged,
                    "uptime": uptime_str,
                    "apps": top_apps
                }
                self.vitals_ready.emit(data)
            except Exception as e:
                print(f"[VitalsWorker ERR] {e}")

            for _ in range(self.interval):
                if not self.running: break
                threading.Event().wait(1)

    def stop(self):
        self.running = False

class WebBackend(QObject):
    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app

    @pyqtSlot(str)
    def receive_text(self, text):
        self.main_app.process_user_input(text)

    @pyqtSlot()
    def trigger_voice(self):
        self.main_app.on_wake_word_button()

class NxoraApp(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # Load Config natively in Python
        self.app_config = {"assistant_name": "Nxora"}
        try:
            with open("config.json", "r") as f:
                self.app_config = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load config.json in main.py: {e}")
            
        self.setWindowTitle(f"{self.app_config.get('assistant_name', 'Nxora')} — Neural Assistant")
        self.setWindowIcon(QIcon(os.path.abspath("ui/logo.png")))
        self.resize(1280, 820)
        self.setMinimumSize(900, 600)
        
        db_name = self.app_config.get('assistant_name', 'Nxora')
        old_db_path_1 = "data/Nxora_memory.db"
        old_db_path_2 = "data/jarvis_memory.db"
        new_db_path = f"data/{db_name}_memory.db"
        
        # Rename legacy databases to preserve memory dynamically
        if os.path.exists(old_db_path_1) and not os.path.exists(new_db_path):
            os.rename(old_db_path_1, new_db_path)
        elif os.path.exists(old_db_path_2) and not os.path.exists(new_db_path):
            os.rename(old_db_path_2, new_db_path)
            
        self.db = MemoryDB(new_db_path)
        
        self.init_ui()
        
        self.ai_worker = AIWorker(self.db)
        self.ai_worker.model_loaded.connect(self.on_model_loaded)
        self.ai_worker.response_ready.connect(self.on_ai_response)
        self.ai_worker.error_occurred.connect(self.show_error)
        self.ai_worker.run_js_signal.connect(self.execute_js_command)
        
        threading.Thread(target=self.ai_worker.load_model, daemon=True).start()
        
        self.wake_worker = WakeWordWorker()
        self.wake_worker.wake_triggered.connect(self.on_wake_word)
        self.wake_worker.start()
        
        self.voice_thread = None
        self.continuous_listening = False
        
        # Real-time System Vitals Monitoring
        self.vitals_worker = VitalsWorker(interval=4)
        self.vitals_worker.vitals_ready.connect(self.on_vitals_data)
        self.vitals_worker.start()
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.web_view = QWebEngineView()
        layout.addWidget(self.web_view)
        
        # Setup the connection channel
        self.channel = QWebChannel()
        self.backend = WebBackend(self)
        self.channel.registerObject("backend", self.backend)
        self.web_view.page().setWebChannel(self.channel)
        
        # Load the HTML Galaxy UI
        html_path = os.path.abspath("ui/index.html")
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))
        self.web_view.loadFinished.connect(self.on_web_loaded)
        
    def on_web_loaded(self, ok):
        if ok:
            # Inject the config data instantly to bypass CORS file:// restriction
            config_json = json.dumps(self.app_config)
            script = f"injectConfig({repr(config_json)});"
            self.web_view.page().runJavaScript(script)
            
            self.load_history()
            
    def appendMessage(self, sender, text, color=""):
        # We escape the text manually to safely pass it to Javascript
        safe_text = json.dumps(text)
        script = f"if (typeof window.appendMessage === 'function') {{ window.appendMessage('{sender}', {safe_text}, '{color}'); }}"
        self.web_view.page().runJavaScript(script)
        
    def update_ui_status(self, text, style):
        safe_text = json.dumps(text)
        script = f"if (typeof window.updateStatus === 'function') {{ window.updateStatus({safe_text}, '{style}'); }}"
        self.web_view.page().runJavaScript(script)

    def load_history(self):
        rows = self.db.load_history()
        for sender, text in rows:
            color = "#38bdf8" if sender != "You" else "#a855f7"
            self.appendMessage(sender, text, color)
            
    def save_user_message(self, text):
        self.db.save_message("You", text)

    def on_model_loaded(self):
        assistant_name = self.app_config.get('assistant_name', 'Nxora')
        self.update_ui_status(f"{assistant_name} Online", "online")
        
    def process_user_input(self, text):
        if not getattr(self.ai_worker, 'is_offline_ready', False):
            self.appendMessage("System", "Model is still loading, please wait...", "system")
            return
            
        self.save_user_message(text)
        self.appendMessage("You", text, "user")
        
        self.update_ui_status("Processing...", "processing")
        
        # Run task
        threading.Thread(target=self.ai_worker.run_task, args=(text,), daemon=True).start()
        
    @safe_call
    def on_ai_response(self, text):
        assistant_name = self.app_config.get('assistant_name', 'Nxora')
        self.appendMessage(assistant_name, text, "")
        speak(text)
        self.update_ui_status(f"{assistant_name} Online", "online")
        if self.continuous_listening:
            self.schedule_listening()

    @pyqtSlot(str)
    def show_error(self, message):
        safe_msg = repr(message)
        self.web_view.page().runJavaScript(f"if (typeof displayError === 'function') {{ displayError({safe_msg}); }} else {{ console.error('UI Error: ' + {safe_msg}); }}")

    @pyqtSlot(str)
    def execute_js_command(self, script):
        """Robust execution of JS on the UI thread."""
        try:
            self.web_view.page().runJavaScript(script)
        except Exception as e:
            print(f"[JS BRIDGE ERR] Failed to execute script: {e}")

    def schedule_listening(self):
        from core.audio import tts_queue
        from PyQt5.QtCore import QMetaObject, Qt, Q_ARG
        def wait_worker():
            tts_queue.join()
            QMetaObject.invokeMethod(self, "start_listening", Qt.QueuedConnection)
        threading.Thread(target=wait_worker, daemon=True).start()

    @pyqtSlot()
    def start_listening(self):
        if not self.continuous_listening:
            return
        if self.voice_thread and self.voice_thread.isRunning():
            return
        self.update_ui_status("Listening...", "listening")
        self.voice_thread = VoiceListenerThread(timeout=15, phrase_time_limit=15)
        self.voice_thread.finished.connect(self.on_voice_heard)
        self.voice_thread.start()

    def on_wake_word_button(self):
        self.continuous_listening = True
        self.start_listening()
        
    def on_wake_word(self):
        try:
            if not getattr(self.ai_worker, 'is_offline_ready', False):
                self.show_error("Model is not ready yet.")
                return
                
            if self.voice_thread and self.voice_thread.isRunning():
                return
                
            self.continuous_listening = True
            self.update_ui_status("Listening...", "listening")
            speak("Yes sir? I am listening boss")
            self.schedule_listening()
        except Exception as e:
            self.show_error(f"Wake word error: {str(e)}")
        
    @pyqtSlot(dict)
    def on_vitals_data(self, data):
        """Pushes system vitals and active apps data to UI."""
        data_json = json.dumps(data)
        script = f"if (typeof window.updateVitals === 'function') {{ window.updateVitals({data_json}); }}"
        self.execute_js_command(script)

    def on_voice_heard(self, text):
        assistant_name = self.app_config.get('assistant_name', 'Nxora')
        self.update_ui_status(f"{assistant_name} Online", "online")
        
        if text == "CONNECTION_ERROR":
            speak("Boss, I am having trouble connecting to the online speech recognition service. Please check your internet connection.")
            self.appendMessage("System", "Speech Recognition failed. No internet connection detected.", "system")
            self.continuous_listening = False
            return
            
        if text == "TIMEOUT":
            # Just go to sleep if there's prolonged silence. No need to spam empty error messages.
            self.continuous_listening = False
            return
            
        if text:
            text_lower = text.lower()
            if any(cmd in text_lower for cmd in ["stop listening", "go to sleep", "mute yourself", "sleep mode", "bye", "goodbye"]):
                speak(f"Going to sleep, Boss. Wake me up when you need me.")
                self.continuous_listening = False
                return
            self.process_user_input(text)
        else:
            if self.continuous_listening:
                self.schedule_listening()

    def closeEvent(self, event):
        """Handle application closure gracefully."""
        print("[NxoraApp] Shutting down...")
        if hasattr(self, 'vitals_worker'):
            self.vitals_worker.stop()
            self.vitals_worker.wait()
        if hasattr(self, 'db'):
            self.db.close()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NxoraApp()
    window.show()
    sys.exit(app.exec_())

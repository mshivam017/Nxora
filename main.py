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
from core.cricket_scraper import CricketScraper

class CricketWorker(QThread):
    data_ready = pyqtSignal(object)
    
    def __init__(self, scraper, match_url=None, interval=1):
        super().__init__()
        self.scraper = scraper
        self.match_url = match_url
        self.interval = interval
        self.running = True
        self.last_ball = None
        self.db = None # Will be set by app

    def run(self):
        while self.running:
            try:
                # 1. Fetch data based on mode
                if self.match_url:
                    data = self.scraper.scrape_match_data(self.match_url)
                else:
                    data = self.scraper.get_all_live_scores()

                if data is not None:
                    # 2. Persist to DB (if provided)
                    if self.db:
                        try:
                            if isinstance(data, list):
                                for m in data: self.db.save_live_match(m)
                            elif isinstance(data, dict):
                                self.db.save_live_match(data)
                        except Exception as e:
                            print(f"[CricketWorker DB ERR] {e}")

                    # 3. Emit for UI Update
                    self.data_ready.emit(data)

                    # 4. Smart Voice Commentary (Throttle to 30s min between speaks)
                    if self.match_url and isinstance(data, dict) and data.get('commentary'):
                        current_ball = data['commentary'][0].get('over')
                        if current_ball != self.last_ball:
                            self.last_ball = current_ball
                            comm_text = data['commentary'][0].get('text', '')
                            if comm_text:
                                speak(f"Update at {current_ball}: {comm_text}")
                else:
                    print("[CricketWorker] Scraping returned None (Timeout or Error)")
                
            except Exception as e:
                print(f"[CricketWorker CRITICAL ERR] {e}")
            
            # 5. Non-blocking sleep that checks self.running status
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

    @pyqtSlot(str)
    def start_live_cricket(self, match_url):
        # Handle cases where match_url might be "null" string from JS or empty
        m_url = str(match_url).strip()
        if m_url.lower() in ["", "null", "undefined"]:
            m_url = None
        print(f"[WebBackend] Start live cricket requested for: {m_url or 'BATCH MODE'}")
        self.main_app.start_cricket_feed(m_url)

    @pyqtSlot()
    def stop_live_cricket(self):
        self.main_app.stop_cricket_feed()

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
        
        self.cricket_scraper = CricketScraper()
        self.cricket_thread = None
        self.current_match_context = ""
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
        
        # Pass match context to AI
        threading.Thread(target=self.ai_worker.run_task, args=(text, self.current_match_context), daemon=True).start()
        
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
        
    def start_cricket_feed(self, match_url=None):
        print(f"[NxoraApp] Starting cricket feed. Match URL: {match_url or 'BATCH'}")
        if self.cricket_thread and self.cricket_thread.isRunning():
            print("[NxoraApp] Stopping existing cricket thread.")
            self.stop_cricket_feed()

        # match_url=None/"" triggers Batch Mode in CricketWorker
        self.cricket_thread = CricketWorker(self.cricket_scraper, match_url, interval=2)
        self.cricket_thread.db = self.db 
        self.cricket_thread.data_ready.connect(self.on_cricket_data)
        self.cricket_thread.start()
        print(f"[NxoraApp] Cricket thread started: {self.cricket_thread.isRunning()}")
        
        mode = "Batch (All Matches)" if not match_url else "Single Match"
        self.update_ui_status(f"Live Feed: {mode}", "online")
        self.appendMessage("System", f"Live cricket feed started in {mode} mode.", "system")

    def stop_cricket_feed(self):
        if self.cricket_thread:
            self.cricket_thread.stop()
            self.cricket_thread.wait()
            self.cricket_thread = None
            self.appendMessage("System", "Live match scraping stopped.", "system")

    @pyqtSlot(object)
    @safe_call
    def on_cricket_data(self, data):
        """Handles incoming cricket data with high resilience."""
        score = "0/0"
        overs = "0"
        comm = "Waiting for data..."
        
        try:
            if data is None: return
            
            print(f"[NxoraApp] on_cricket_data received: {type(data)}")
            
            # Update UI via Javascript bridge
            data_json = json.dumps(data)
            script = None
            
            if isinstance(data, list):
                script = f"if (typeof window.showAllMatches === 'function') {{ window.showAllMatches({data_json}); }} else {{ console.error('showAllMatches not found'); }}"
            elif isinstance(data, dict) or hasattr(data, "get"):
                script = f"if (typeof window.showIPLDashboard === 'function') {{ window.showIPLDashboard({data_json}); }} else {{ console.error('showIPLDashboard not found'); }}"
                
                # Build ultra-accurate context for AI
                score = data.get('score') or "Live"
                overs = data.get('overs') or "0"
                if data.get('commentary') and len(data['commentary']) > 0:
                    comm = data['commentary'][0].get('text', '')

                batters_list = data.get('batters', [])
                batters = ", ".join([f"{b.get('name', 'Unknown')} {b.get('runs', 0)}({b.get('balls', 0)})" for b in (batters_list if isinstance(batters_list, list) else [])])
                
                bowlers_list = data.get('bowlers', [])
                bowlers = ", ".join([f"{b.get('name', 'Unknown')} {b.get('overs', 0)}-{b.get('runs', 0)}-{b.get('wickets', 0)}" for b in (bowlers_list if isinstance(bowlers_list, list) else [])])
                
                history_list = data.get('history', [])
                history = ", ".join([str(h) for h in (history_list if isinstance(history_list, list) else [])])
                
                match_info = f"NEURAL MATCH STATE:\n"
                match_info += f"Series State: {data.get('matchState', 'Live Action')}\n"
                match_info += f"Match: {data.get('teamA', 'Unknown')} vs {data.get('teamB', 'Unknown')}\n"
                match_info += f"Score: {score} ({overs} ov)\n"
                match_info += f"RR: {data.get('runRate', '0.00')} | Req: {data.get('reqRunRate', 'N/A')}\n"
                match_info += f"Partnership: {data.get('partnership', 'N/A')}\n"
                match_info += f"Batting: {batters or 'N/A'}\n"
                match_info += f"Bowling: {bowlers or 'N/A'}\n"
                match_info += f"Visual Momentum: {history or '...'}\n"
                match_info += f"Pulse (Latest): {comm}\n"
                match_info += f"STYLE: Professional cricket analyst, energetic, seductive, high-momentum commentary."
                
                self.current_match_context = match_info
            
            if script:
                self.execute_js_command(script)
                
        except Exception as e:
            print(f"[UI UPDATE ERR] Critical failure in on_cricket_data: {e}")
            import traceback
            traceback.print_exc()

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
        self.stop_cricket_feed()
        if hasattr(self, 'db'):
            self.db.close()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NxoraApp()
    window.show()
    sys.exit(app.exec_())

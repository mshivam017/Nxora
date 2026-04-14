import pyttsx3
import speech_recognition as sr
import queue
import threading
import re
import time
from PyQt5.QtCore import QThread, pyqtSignal

# --- TTS Engine ---
tts_queue = queue.Queue()

def tts_worker():
    while True:
        text = tts_queue.get()
        if text is None:
            break
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty('voices')
            for voice in voices:
                if "david" in voice.name.lower() or "male" in voice.name.lower():
                    engine.setProperty('voice', voice.id)
                    break
            engine.setProperty('rate', 200)
            engine.say(text)
            engine.runAndWait()
            del engine
        except Exception as e:
            print(f"Speech error: {e}")
        tts_queue.task_done()

threading.Thread(target=tts_worker, daemon=True).start()

def speak(text):
    try:
        # Pre-process text so TTS doesn't read out actual code blocks, URLs, or weird markdown
        
        # 1. Remove markdown code blocks completely
        clean_text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        # 2. Remove inline code
        clean_text = re.sub(r'`.*?`', '', clean_text)
        # 3. Remove raw HTML tags
        clean_text = re.sub(r'<[^>]+>', '', clean_text)
        # 4. Remove standard markdown symbols
        clean_text = re.sub(r'[*#_~>\[\]\(\)]', '', clean_text)
        
        clean_text = clean_text.encode('ascii', 'ignore').decode('ascii')
        
        if clean_text.strip():
            tts_queue.put(clean_text)
    except Exception as e:
        print(f"Speech queue error: {e}")

# --- Active Listening Thread ---
class VoiceListenerThread(QThread):
    finished = pyqtSignal(str)
    
    def __init__(self, timeout=5, phrase_time_limit=8):
        super().__init__()
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        
    def run(self):
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.5
        text = ""
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=self.timeout, phrase_time_limit=self.phrase_time_limit)
                text = recognizer.recognize_google(audio)
        except sr.WaitTimeoutError:
            text = "TIMEOUT"
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            text = "CONNECTION_ERROR"
        except Exception as e:
            pass
        self.finished.emit(text)

import pvporcupine
import struct
import pyaudio

# --- Background Wake Word Thread ---
class WakeWordWorker(QThread):
    wake_triggered = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        
    def run(self):
        porcupine = None
        paud = None
        audio_stream = None
        try:
            # Load the proprietary NxoraAI/Nxora model from V1
            porcupine = pvporcupine.create(
                access_key="DF0e72o7J/wbraSNgieuTqHzj5IDoyNf0MZvy86fOEEQZUIDzA+zxw==",
                keyword_paths=["./models/Next-Sora_en_windows_v4_0_0.ppn"]
            )
            paud = pyaudio.PyAudio()
            audio_stream = paud.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length
            )
            
            while True:
                pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
                
                keyword_index = porcupine.process(pcm)
                if keyword_index >= 0:
                    self.wake_triggered.emit()
                    
        except Exception as e:
            print(f"Wake Word engine crashed: {e}")
            time.sleep(1)
        finally:
            if porcupine is not None:
                porcupine.delete()
            if audio_stream is not None:
                audio_stream.close()
            if paud is not None:
                paud.terminate()

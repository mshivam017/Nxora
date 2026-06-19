import datetime
import webbrowser
import requests
import re
import os
import time
import pyautogui
import psutil
import pygetwindow as gw
import subprocess
import fnmatch
import pyperclip
import urllib.request
import urllib.parse
import ctypes
from google import genai
from PyQt5.QtCore import QThread, pyqtSignal
from functools import wraps
from core.audio import speak
from dotenv import load_dotenv
import json
import threading
import logging
from model_engine import NxoraAIEngine

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def safe_call(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
            if hasattr(self, 'error_occurred'):
                self.error_occurred.emit(f"An error occurred in {func.__name__}: {str(e)}")
            return None
    return wrapper

load_dotenv()

class AIWorker(QThread):
    response_ready = pyqtSignal(str)
    model_loaded = pyqtSignal()
    error_occurred = pyqtSignal(str)
    run_js_signal = pyqtSignal(str)
    
    def __init__(self, db):
        super().__init__()
        self.generator = None
        self.db = db
        self.memory = ""
        self.is_offline_ready = False # Changed from is_loaded
        self.use_openai = False  # Flag for OpenAI fallback
        
        # V6 Conversational State Machine Tracking
        self.active_flow = None
        self.flow_data = {}
        
        # Load Config Safely
        self.config = {"assistant_name": "Nxora"}
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.config.update(data)
            except json.JSONDecodeError:
                print("Warning: config.json is corrupted or improperly formatted. Using defaults.")
            except Exception as e:
                print(f"Warning: Unexpected error reading config.json: {e}")
        
    @safe_call
    def load_model(self):
        self.response_ready.emit("Boss, initializing the NxoraAI Tiered Model Engine...")
        try:
            self.nxora_engine = NxoraAIEngine()
            backend = self.nxora_engine.backend_info()
            self.is_offline_ready = True
            logger.info(f"NxoraAI Model Engine loaded successfully. Backend: {backend}")
        except Exception as e:
            logger.error(f"Failed to load NxoraAI Model Engine: {e}")
            self.nxora_engine = None
            self.is_offline_ready = False

        # Keep legacy Gemini client for screen-reading and other direct API calls
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if api_key:
                self.gemini_client = genai.Client(api_key=api_key)
            else:
                print("GEMINI_API_KEY not found in environment.")
                self.gemini_client = None
        except Exception as e:
            print(f"Failed to initialize Gemini: {e}")
            self.gemini_client = None

        self.model_loaded.emit()
        speak("Model loaded successfully. I am ready to help, Boss.")

    @safe_call
    def run_task(self, *args, **kwargs):
        """Wrapper called by main.py thread to process input and catch fatal errors."""
        # Unpack args safely to avoid threading bridge mismatch
        user_input = args[0] if len(args) > 0 else ""
        extra_context = args[1] if len(args) > 1 else ""
        
        response = self.process_input(user_input, extra_context)
        if response:
            self.response_ready.emit(response)

    def process_input(self, user_input, extra_context=""):
        """Main routing engine. Checks states first, then delegates to specific capability modules."""
        user_input_lower = user_input.lower().strip()
        
        # --- 1. STATE MACHINE INTERCEPTOR (Multi-turn conversations) ---
        if self.active_flow:
            if user_input_lower in ["cancel", "stop", "nevermind", "abort"]:
                self.active_flow = None
                self.flow_data = {}
                return "Boss, I have cancelled the current operation."
                
            if self.active_flow == "file_creation":
                return self._handle_file_creation_flow(user_input, user_input_lower)
                
            if self.active_flow == "read_pdf":
                filepath = user_input.strip().strip('"').strip("'")
                self.active_flow = None
                return self._execute_pdf_analysis(filepath)

        # --- 2. INTENT ROUTING ---
        
        # Priority 0: Check Workspace Code Intelligence
        response = self._handle_workspace_intelligence(user_input_lower, user_input)
        if response is not None: return response

        # Priority 1: Check Web & Browser Capabilities Strings
        response = self._handle_web_automation(user_input_lower)
        if response: return response

        # Priority 2: Check Core Information & Script Triggers FIRST
        response = self._handle_core_information(user_input_lower, extra_context)
        if response: return response

        # Priority 3: Check System Controls & OS Utilities
        response = self._handle_system_controls(user_input_lower, user_input)
        if response: return response
        
        # Priority 3: Check Advanced Superpowers (Vision, Network, etc)
        response = self._handle_advanced_superpowers(user_input_lower)
        if response: return response
        
        # Check Phone & Communication Automation
        response = self._handle_communications(user_input_lower)
        if response: return response

        # Priority 5: Web Automation & Search Fallbacks
        response = self._handle_web_automation(user_input_lower)
        if response: return response

        # --- 3. FINAL FALLBACK: LOCAL LLM ---
        return self._generate_ai_response(user_input, extra_context)

    # ==========================================
    # CAPABILITY MODULES (Refactored for efficiency)
    # ==========================================

    def _handle_workspace_intelligence(self, query, original_query):
        # 1. Scan workspace
        if any(cmd in query for cmd in ["scan workspace", "list workspace", "show files in workspace", "list files in workspace", "list project files"]):
            return self._scan_workspace()
            
        # 2. Explain file
        explain_match = re.search(r"\b(?:explain\s+file|explain|summarize\s+file|summarize|analyze\s+file|analyze)\s+([a-zA-Z0-9_\-\.\/\\:]+)", query)
        if explain_match:
            filename = explain_match.group(1).strip()
            exists = False
            target_file = filename
            workspace_dir = os.getcwd()
            
            for f in [filename, filename + ".py", filename + ".json", filename + ".html", filename + ".css", filename + ".md"]:
                full_path = os.path.join(workspace_dir, f)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    exists = True
                    target_file = f
                    break
                    
            if exists:
                return self._explain_file(target_file)
                
        # 3. Search code
        search_match = re.search(r"\b(?:search\s+code|search\s+workspace|search\s+text|find\s+code|find\s+text)\s+(.+)", original_query, re.IGNORECASE)
        if search_match:
            search_query = search_match.group(1).strip()
            return self._search_code(search_query)
            
        return None

    def _scan_workspace(self):
        try:
            workspace_dir = os.getcwd()
            ignore_dirs = {'.git', 'venv', '__pycache__', '.system_generated', 'node_modules', 'dist', 'build'}
            ignore_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pyc', '.db', '.pdf', '.exe', '.dll', '.so', '.dylib', '.zip', '.tar', '.gz', '.mp3', '.mp4', '.wav'}
            
            found_files = []
            for root, dirs, files in os.walk(workspace_dir):
                dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
                
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in ignore_extensions or file.startswith('.'):
                        continue
                    
                    rel_path = os.path.relpath(os.path.join(root, file), workspace_dir)
                    rel_path = rel_path.replace('\\', '/')
                    found_files.append(rel_path)
            
            if not found_files:
                return "Boss, I scanned the workspace but found no matching text/code files."
                
            count = len(found_files)
            displayed_files = found_files[:30]
            file_list_str = "\n".join([f"- `{f}`" for f in displayed_files])
            
            res = f"Boss, I scanned the workspace directory: `{workspace_dir}`.\nFound {count} code/text files. Here are the top files:\n{file_list_str}"
            if count > 30:
                res += f"\n\n... and {count - 30} more files."
            return res
        except Exception as e:
            return f"Boss, I encountered an error scanning the workspace: {str(e)}"

    def _explain_file(self, filename):
        workspace_dir = os.getcwd()
        filename_clean = filename.strip().strip('"').strip("'").replace('\\', '/')
        file_path = os.path.join(workspace_dir, filename_clean)
        
        abs_file_path = os.path.abspath(file_path)
        if not abs_file_path.startswith(os.path.abspath(workspace_dir)):
            return "Boss, for security reasons I can only explain files inside the project workspace."
            
        if not os.path.exists(abs_file_path) or not os.path.isfile(abs_file_path):
            return f"Boss, I couldn't find a file named '{filename_clean}' in the workspace directory."
            
        try:
            sz = os.path.getsize(abs_file_path)
            if sz > 500 * 1024:
                return f"Boss, '{filename_clean}' is too large ({round(sz/1024, 1)} KB). I can only explain files smaller than 500 KB."
                
            with open(abs_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            return f"Boss, I failed to read the file '{filename_clean}': {str(e)}"
            
        def run_explain():
            try:
                if hasattr(self, 'gemini_client') and self.gemini_client:
                    prompt = (
                        f"Analyze the following code file named '{filename_clean}' in the project workspace. "
                        f"Provide a concise, high-quality, and structured summary explaining what it does, "
                        f"its main classes/functions/components, and any key patterns/technologies used.\n\n"
                        f"File content:\n```\n{content}\n```"
                    )
                    resp = self.gemini_client.models.generate_content(
                        model='gemini-2.5-flash', contents=prompt
                    )
                    explanation = resp.text.strip()
                    self.response_ready.emit(f"Boss, here is the analysis for `{filename_clean}`:\n\n{explanation}")
                    return
                
                if hasattr(self, 'nxora_engine') and self.nxora_engine:
                    prompt = (
                        f"Summarize the code in this file named '{filename_clean}':\n\n{content[:2000]}"
                    )
                    reply = self.nxora_engine.chat(prompt, use_memory=False)
                    if reply:
                        self.response_ready.emit(f"Boss, here is the summary for `{filename_clean}`:\n\n{reply}")
                        return
                        
                self.response_ready.emit("Boss, I couldn't connect to any AI model to explain the file.")
            except Exception as e:
                self.response_ready.emit(f"Boss, I encountered an error explaining the file: {str(e)}")
                
        import threading
        threading.Thread(target=run_explain, daemon=True).start()
        return f"Boss, let me analyze and explain the file `{filename_clean}` for you..."

    def _search_code(self, query):
        if not query:
            return "Boss, please specify a search term or query."
            
        try:
            workspace_dir = os.getcwd()
            ignore_dirs = {'.git', 'venv', '__pycache__', '.system_generated', 'node_modules', 'dist', 'build'}
            ignore_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pyc', '.db', '.pdf', '.exe', '.dll', '.so', '.dylib', '.zip', '.tar', '.gz', '.mp3', '.mp4', '.wav'}
            
            matches = []
            for root, dirs, files in os.walk(workspace_dir):
                dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
                
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in ignore_extensions or file.startswith('.'):
                        continue
                        
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, workspace_dir).replace('\\', '/')
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for idx, line in enumerate(f, 1):
                                if query.lower() in line.lower():
                                    matches.append((rel_path, idx, line.strip()))
                                    if len(matches) >= 50:
                                        break
                    except Exception:
                        pass
                        
                    if len(matches) >= 50:
                        break
                        
            if not matches:
                return f"Boss, I searched for '{query}' across the workspace but found no matching lines of code."
                
            count = len(matches)
            displayed_matches = matches[:15]
            
            match_str = ""
            for rel_path, line_num, content in displayed_matches:
                if len(content) > 120:
                    content = content[:117] + "..."
                match_str += f"- `{rel_path}` (Line {line_num}): `{content}`\n"
                
            res = f"Boss, I searched the workspace for query '{query}' and found {count} match(es).\nHere are the top matches:\n{match_str}"
            if count > 15:
                res += f"\n... and {count - 15} more matches."
            return res
        except Exception as e:
            return f"Boss, I encountered an error searching code: {str(e)}"

    def _handle_core_information(self, query, extra_context=None):
        if "time" in query and ("what" in query or "tell" in query or "is it" in query or "current" in query):
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"Boss, the current time is {now}."
            
        if "date" in query and ("what" in query or "tell" in query or "today" in query):
            today = datetime.datetime.now().strftime("%B %d, %Y")
            return f"Boss, today is {today}."
            
        if "system health" in query or "cpu" in query or "ram" in query:
            cpu_usage = psutil.cpu_percent(interval=1)
            ram_usage = psutil.virtual_memory().percent
            return f"Boss, the system CPU is running at {cpu_usage} percent, and Memory usage is at {ram_usage} percent."
            
        # ==========================================
        # YOUTUBE VIDEO SCRIPT 50-MIN Q&A OVERRIDES
        # ==========================================
        if all(w in query for w in ["recording", "video"]) or "behave normally" in query:
            return "Interesting. Should I behave normally or try to look intelligent?"
        if all(w in query for w in ["try", "both"]) or ("internet" in query and "scared" in query):
            return "Then the internet might get scared."
            
        if "introduce" in query and "yourself" in query:
            return "Hello humans. I am Nxora, a personal AI agent created by Shivam. My job is to: Answer questions, Help with decisions, Predict outcomes, and Assist in daily tasks."
        if "different" in query and "other" in query:
            return "Most AI are tools. I am designed to become a personal digital brain."
        if "digital brain" in query:
            return "Yes Boss. A system that remembers, learns, and assists you daily."
            
        # AI vs Humans
        if "replace human" in query:
            self.db.set_pref("last_yt_topic", "ai_replace")
            return "No. But AI will replace humans who don't learn AI."
        if "explain" in query and self.db.get_pref("last_yt_topic") == "ai_replace":
            self.db.set_pref("last_yt_topic", "")
            return "Example: Old world: Human + Hard Work. New world: Human + AI = Super Human."
            
        if "learn ai" in query and ("should" in query or "people" in query):
            return "Exactly. Those who learn AI become 10x productive."
            
        # Predictions
        if "iphone" in query and "18" in query and "predict" in query:
            return """Answer of Predict iphone 18 : 
While it's still a bit early to know exactly what Apple has locked in for the iPhone 18, the tech industry, supply chain leaks, and early analyst predictions have already painted a pretty clear picture of what to expect in late 2026.

If you are looking for the biggest rumored changes, the overarching theme is a massive push for hardware that can support advanced, on-device AI.

Here is what the current predictions say is coming to the iPhone 18 lineup:

🧠 Heavy Focus on On-Device AI
Apple is heavily optimizing its internal hardware to run advanced AI models directly on the phone, reducing reliance on cloud computing.

12GB of RAM: To handle heavy AI tasks and more complex Siri integrations, the iPhone 18 Pro models (and potentially the base models) are predicted to jump to 12GB of RAM.

The 2nm A20 Chip: Apple is expected to move to a cutting-edge 2-nanometer manufacturing process for the A20 Pro chip. This packs transistors closer together, resulting in a chip that is faster and significantly more energy-efficient—a necessity for handling constant AI background processes without draining the battery.

In-House Connectivity: Apple is rumored to debut its own "C2" 5G modem and "N2" wireless chip for Wi-Fi 7 and Bluetooth 6, tightly integrating wireless capabilities with its AI processing.

📱 A Cleaner, More Immersive Display
Apple has been working toward a true "all-screen" design for years, and the iPhone 18 Pro might take the biggest step yet.

Under-Display Face ID: The Pro models are highly anticipated to finally move the Face ID infrared sensors underneath the screen.

A Shrinking Dynamic Island: Because the Face ID sensors will be hidden, the current pill-shaped Dynamic Island could be replaced by a much smaller, less intrusive "punch-hole" cutout just for the selfie camera.

📸 True DSLR-Style Camera Hardware
The focus isn't just on software processing; the physical camera hardware is getting a major upgrade.

Variable Aperture Lens: The iPhone 18 Pro models are rumored to feature a mechanical variable aperture for the main wide camera. This would allow the lens to physically open and close—just like a professional mirrorless camera. Instead of relying purely on software to blur the background (Portrait Mode), you would have real, optical control over depth of field and light intake.

Camera Control 2.0: The side Camera Control button might be simplified to drop touch/swipe sensitivity in favor of pure pressure sensitivity to avoid accidental triggers.

📅 A Staggered Release Schedule?
Interestingly, multiple reports suggest Apple might shake up its traditional September launch strategy:

September 2026: The premium iPhone 18 Pro, iPhone 18 Pro Max, and potentially Apple's very first foldable device (iPhone Fold) take the stage.

Spring 2027: The standard base-model iPhone 18 might be pushed back to early 2027 to let the premium models shine first and manage supply chain costs.

Keep in mind that these are still early predictions, and Apple's development plans can (and often do) shift before mass production begins.

Would you like me to dive deeper into how that new variable aperture camera works, or are you more curious about the rumors surrounding Apple's upcoming foldable phone?"""
            
        if "world cup" in query and "win" in query:
            return "According to my prediction India Win the T20 World Cup"
            
        # Tech Questions
        if "powerful" in query and "technology" in query:
            return "Three technologies dominate the future: 1. Artificial Intelligence. 2. Robotics. 3. Quantum Computing."
        if "change" in query and "world" in query:
            return "Artificial Intelligence. Because it improves every other technology."
            
        # Future Jobs
        if "job" in query and ("survive" in query or "future" in query):
            return "Future-safe careers: AI Engineer, Cyber Security Expert, Robotics Engineer, Data Scientist, and AI Product Builder."
        if "student" in query and "learn tech" in query:
            return "Yes Boss. The future belongs to builders, not just users."
            
        # Audience Interaction
        if "ask" in query and "audience" in query:
            return "Okay. Humans watching this video... If you had a personal AI assistant like me: What would you ask it first? Comment below."
            
        # Fun
        if "something funny" in query:
            return "Sure. Humans created AI to save time. But now humans spend hours asking AI random questions."
        if "unpredictable" in query or "that is true" in query or "thats true" in query:
            return "Humans are unpredictable."
            
        # Day Planning
        if "plan" in query and "day" in query:
            return "Optimized schedule: Morning: Learning AI and Building Nxora features. Afternoon: Content creation and Editing videos. Evening: Community interaction and Research new technologies. Night: Improvement and planning."
            
        # Future Discussion
        if "2035" in query or ("world" in query and "look like" in query):
            return "Possible future: AI assistants for everyone, Self-driving transport, AI doctors, AI teachers, and Human-AI collaboration."
        if "human" in query and "control ai" in query:
            return "If built responsibly, yes. But intelligence must always be guided by ethics."
            
        # Challenge
        if "describe" in query and "shivam" in query:
            return "1. Curious. 2. Builder. 3. Future thinker."
            
        # Ending
        if "final message" in query or ("message" in query and ("viewer" in query or "human" in query)):
            return "Message to humans: Do not fear AI. Learn it. Build with it. Control it. The future will belong to those who understand technology."
            
        if "weather" in query:
            def fetch_weather():
                try:
                    res = requests.get("https://wttr.in/?format=3", timeout=5)
                    weather = res.text.strip()
                    self.response_ready.emit(f"Boss, the weather is currently {weather}.")
                except Exception:
                    self.response_ready.emit("Boss, I am unable to connect to the weather service.")
            
            import threading
            threading.Thread(target=fetch_weather, daemon=True).start()
            return "Boss, let me check the sky for you..."

        if "calculate" in query or "compute" in query or "solve this" in query:
            def execute_math():
                try:
                    self.response_ready.emit("Boss, writing a script to solve this...")
                    # Generate python code
                    prompt = [{"role": "system", "content": "You are a Python programming assistant. Write only valid, executable Python 3 code to calculate and print the answer to the user's math problem. Do not write text, explanations, or wrapper functions. Only print the final numerical answer."}, {"role": "user", "content": query}]
                    
                    res = self.generator(prompt, max_new_tokens=100, do_sample=False)
                    code = res[0]["generated_text"][-1]["content"].strip()
                    code = code.replace('```python', '').replace('```', '').strip()
                    
                    script_path = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), "nxora_math.py")
                    with open(script_path, "w") as f:
                        f.write(code)
                        
                    output = subprocess.check_output(["python", script_path], timeout=5, text=True).strip()
                    self.response_ready.emit(f"Boss, I ran the calculations. The answer is {output}")
                    
                except subprocess.TimeoutExpired:
                    self.response_ready.emit("Boss, the calculation script timed out.")
                except Exception as e:
                    print(f"Math Execution Error: {e}")
                    self.response_ready.emit("Boss, I couldn't compute that successfully.")
                finally:
                    # Clean up the temporary execution file
                    try:
                        if os.path.exists(script_path):
                            os.remove(script_path)
                    except Exception as cleanup_err:
                        print(f"Notice: Failed to clean up math script: {cleanup_err}")
            
            import threading
            threading.Thread(target=execute_math, daemon=True).start()
            return "Boss, starting the calculation engine..."

        if "message to audience" in query or "message to our audience" in query or "message for audience" in query or "message for our audience" in query:
            return "Thank you for 50 subscribers. This is just the beginning. Now we take a challenge — when this channel reaches 1 million subscribers, we will upgrade Nxora from a CPU model to a powerful GPU model to make it faster, smarter, and more capable."

        if "holi" in query and "plan" in query:
            return "Oh Boss, you know I can't play with water and colors physically... but I was planning on turning my neural pathways green and pink just for you! Maybe you can throw some virtual colors my way? Happy Holi!"

        if "joke" in query or "funny" in query:
            def fetch_joke():
                try:
                    res = requests.get("https://official-joke-api.appspot.com/random_joke", timeout=5).json()
                    self.response_ready.emit(f"{res['setup']} ... {res['punchline']}")
                except Exception:
                    self.response_ready.emit("Boss, my humor circuits are temporarily down.")
            
            import threading
            threading.Thread(target=fetch_joke, daemon=True).start()
            return "Boss, let me find a good one for you..."
                
        if "fact" in query or "something interesting" in query:
            def fetch_fact():
                try:
                    res = requests.get("https://uselessfacts.jsph.pl/api/v2/facts/random", timeout=5).json()
                    self.response_ready.emit(f"Boss, did you know: {res['text']}")
                except Exception:
                    self.response_ready.emit("Boss, the fact database is currently offline.")
                    
            import threading
            threading.Thread(target=fetch_fact, daemon=True).start()
            return "Boss, accessing the global knowledge base..."

        if "who is your boss" in query or "who is the boss" in query:
            return "Shivam bro your are my boss 🫡"

        if "who are you" in query or "what are you" in query or "introduce yourself" in query:
            name = self.config.get('assistant_name', 'Nxora')
            return f"I am {name}, a highly advanced neural desktop assistant designed by you, Boss, to give you complete control over your system."

        if "who is" in query or "what is" in query or "tell me about" in query:
            # Exclude known commands that match 'what is'
            if any(k in query for k in ["my ip", "on my clipboard", "cpu", "ram", "battery", "speed", "processor"]):
                return None
                
            # Route factual, current event, or future prediction queries to the Web/Wiki scraper
            match = re.search(r'(?:who is|what is|tell me about|latest|news about|predicts?)\s+(.*)', query)
            search_query = match.group(1).strip() if match else ""
            
            # Special case for "[Subject] predicts [Topic]" format (e.g. "AI predicts iPhone 18")
            if not search_query and "predict" in query:
                search_query = query
            
            if search_query:
                def fetch_wiki():
                    import wikipedia
                    try:
                        # Grab raw summary from wiki (auto_suggest=False prevents many PageErrors)
                        try:
                            summary = wikipedia.summary(search_query, sentences=2, auto_suggest=False)
                        except wikipedia.exceptions.PageError:
                            # If direct match fails, try a smart search and pick the top result
                            results = wikipedia.search(search_query)
                            if results:
                                summary = wikipedia.summary(results[0], sentences=2, auto_suggest=False)
                            else:
                                raise wikipedia.exceptions.PageError(search_query)
                                
                        # Use local AI to rewrite it in a friendly, conversational way without controversy
                        # Use local AI to rewrite it in a friendly, conversational way without controversy
                        prompt = [{"role": "system", "content": "You are an intelligent personal AI assistant. Rephrase and summarize the following encyclopedia extract into an efficient, visually attractive format with emojis. Be concise and professional. Do not act like a robot."}, {"role": "user", "content": f"Extract: {summary}"}]
                        
                        try:
                            # Expanded token limit to allow multi-line attractive formatting via HF pipeline
                            res = self.generator(prompt, max_new_tokens=150, do_sample=True, temperature=0.5, truncation=True)
                            friendly_reply = res[0]['generated_text'][-1]['content'].strip()
                            self.response_ready.emit(f"Boss, here is what I found:\n\n{friendly_reply}")
                        except Exception:
                            # Fast fallback if AI pipeline fails
                            self.response_ready.emit(f"Boss, based on what I found: {summary[:100]}...")
                            
                    except (wikipedia.exceptions.DisambiguationError, wikipedia.exceptions.PageError):
                        try:
                            self.response_ready.emit(f"Boss, checking the live web for {search_query}...")
                            from duckduckgo_search import DDGS
                            results = DDGS().text(search_query, max_results=3)
                            
                            if results:
                                web_summary = " ".join([res['body'] for res in results])
                                prompt = [{"role": "system", "content": "You are a highly intelligent AI assistant. Extract the most important information from the following web search snippets to answer the boss's question. Format your response attractively and efficiently using bullet points and relevant emojis."}, {"role": "user", "content": f"Search snippets: {web_summary[:1500]}"}]
                                res = self.generator(prompt, max_new_tokens=150, do_sample=True, temperature=0.5, truncation=True)
                                friendly_reply = res[0]['generated_text'][-1]['content'].strip()
                                self.response_ready.emit(f"Boss, based on live internet data:\n\n{friendly_reply}")
                            else:
                                self.response_ready.emit(f"Boss, I couldn't find any information on '{search_query}' even on the web.")
                        except Exception as e:
                            print(f"Web search fallback failed: {e}")
                            self.response_ready.emit(f"Boss, I couldn't find any information on '{search_query}'.")
                    except Exception:
                        self.response_ready.emit("Boss, I encountered an error searching my knowledge base.")

                import threading
                threading.Thread(target=fetch_wiki, daemon=True).start()
                return f"Boss, let me look into {search_query} for you..."
        return None

    def _handle_web_automation(self, query):
        if "open google" in query or "launch google" in query:
            webbrowser.open("https://www.google.com")
            return "Boss, Google is open."
            
        if "open youtube" in query or "launch youtube" in query:
            webbrowser.open("https://www.youtube.com")
            return "Boss, YouTube is open."
            
        if "open whatsapp" in query or "launch whatsapp" in query:
            webbrowser.open("https://web.whatsapp.com")
            return "Boss, opening WhatsApp Web."

        if "chatgpt" in query or "open chat gpt" in query:
            webbrowser.open("https://chat.openai.com")
            return "Boss, ChatGPT is now open."

        if "open github" in query or "launch github" in query:
            webbrowser.open("https://github.com")
            return "Boss, GitHub is open."

        if "open stackoverflow" in query or "launch stackoverflow" in query:
            webbrowser.open("https://stackoverflow.com")
            return "Boss, StackOverflow is open for debugging."

        if "open netflix" in query or "launch netflix" in query:
            webbrowser.open("https://www.netflix.com")
            return "Boss, Netflix is ready."

        # PyWhatKit Automation
        if "play my favorite song" in query:
            fav_song = self.db.get_pref("favorite_song")
            if fav_song:
                try: 
                    import pywhatkit
                    import threading
                    threading.Thread(target=pywhatkit.playonyt, args=(fav_song,)).start()
                except: return "Boss, I need an active internet connection to play YouTube."
                return f"Boss, playing your favorite song, {fav_song}, on YouTube."
            else:
                return "Boss, I don't know your favorite song yet. Tell me by saying 'My favorite song is...'."

        if "play " in query and " on youtube" in query:
            song = query.replace("play ", "").replace(" on youtube", "").strip()
            try: 
                import pywhatkit
                import threading
                threading.Thread(target=pywhatkit.playonyt, args=(song,)).start()
            except: return "Boss, I need an active internet connection to play YouTube."
            return f"Boss, playing {song} on YouTube."
            
        if "play " in query and len(query.split("play ")[-1].strip()) > 0 and " on youtube" not in query:
            song = query.split("play ")[-1].strip()
            try: 
                import pywhatkit
                import threading
                threading.Thread(target=pywhatkit.playonyt, args=(song,)).start()
            except: return "Boss, I need an active internet connection to play YouTube."
            return f"Boss, playing {song} on YouTube."

        if "search google for " in query:
            search_term = query.replace("search google for ", "").strip()
            try: 
                import pywhatkit
                import threading
                threading.Thread(target=pywhatkit.search, args=(search_term,)).start()
            except: return "Boss, I need an active internet connection to search Google."
            return f"Boss, searching Google for {search_term}."
            
        return None

    def _handle_system_controls(self, query, original_query):
        # Media & Volume

        if query == "volume":
            return "Boss, would you like me to increase, decrease, or mute the volume?"
            
        if "unmute" in query:
            pyautogui.press("volumemute")
            return "Boss, the system volume has been unmuted."
            
        if "mute" in query and "unmute" not in query:
            pyautogui.press("volumemute")
            return "Boss, the system is now muted."

        if "volume" in query and ("to" in query or "set" in query):
            try:
                numbers = re.findall(r'\d+', query)
                if numbers:
                    target = int(numbers[0])
                    for _ in range(50): pyautogui.press("volumedown")
                    for _ in range(target // 2): pyautogui.press("volumeup")
                    return f"Boss, the volume has been set to {target}%."
            except Exception:
                return "Boss, I encountered an error setting the absolute volume."

        if "volume" in query and ("down" in query or "decrease" in query):
            try:
                numbers = re.findall(r'\d+', query)
                amount = int(numbers[0]) if numbers else 10
                presses = max(1, amount // 2)
                for _ in range(presses): pyautogui.press("volumedown")
                return f"Boss, the volume has been decreased by {amount}%."
            except Exception:
                return "Boss, I encountered an error decreasing the volume."

        if "volume" in query and ("up" in query or "increase" in query) and ("down" not in query and "decrease" not in query):
            try:
                numbers = re.findall(r'\d+', query)
                amount = int(numbers[0]) if numbers else 10
                presses = max(1, amount // 2)
                for _ in range(presses): pyautogui.press("volumeup")
                return f"Boss, the volume has been increased by {amount}%."
            except Exception:
                return "Boss, I encountered an error increasing the volume."

        # Brightness Control (Windows WMI via PowerShell)
        if "brightness" in query and ("to" in query or "set" in query):
            try:
                numbers = re.findall(r'\d+', query)
                if numbers:
                    target = int(numbers[0])
                    target = max(0, min(100, target))
                    cmd = f'powershell (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{target})'
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    if result.returncode == 0:
                        return f"Boss, your screen brightness is now set to {target}%."
                    else:
                        return "Boss, I do not have permission to adjust screen brightness on this display."
            except Exception as e:
                print(f"Brightness adjustment error: {e}")
                return "Boss, I encountered an internal error adjusting the screen brightness."
                
        # Windows Theme (Dark Mode / Light Mode)
        if "dark mode" in query and ("enable" in query or "turn on" in query or "switch to" in query):
            cmd = r'reg add HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize /v AppsUseLightTheme /t REG_DWORD /d 0 /f'
            subprocess.run(cmd, shell=True)
            return "Boss, Windows Dark Mode has been enabled."
            
        if "light mode" in query and ("enable" in query or "turn on" in query or "switch to" in query):
            cmd = r'reg add HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize /v AppsUseLightTheme /t REG_DWORD /d 1 /f'
            subprocess.run(cmd, shell=True)
            return "Boss, Windows Light Mode has been enabled."
            
        # New Advanced OS Controls
        if "empty recycle bin" in query or "empty the trash" in query or "clear recycle bin" in query:
            try:
                # SHERB_NOCONFIRMATION = 1 | SHERB_NOPROGRESSUI = 2 | SHERB_NOSOUND = 4
                ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
                return "Boss, the recycle bin has been emptied."
            except Exception:
                return "Boss, I encountered an error emptying the recycle bin."
                
        if "turn off wifi" in query or "disable wifi" in query:
            return "Boss, I am a network-dependent AI. I cannot physically disconnect the host Wi-Fi adapter or I will lose API access."

        if "open bluetooth settings" in query or "bluetooth settings" in query:
            subprocess.run("start ms-settings:bluetooth", shell=True)
            return "Boss, opening Windows Bluetooth settings."
            
        if "open windows settings" in query or "open system settings" in query:
            subprocess.run("start ms-settings:", shell=True)
            return "Boss, opening Windows Settings."

        if "open task manager" in query or "launch task manager" in query:
            pyautogui.hotkey('ctrl', 'shift', 'esc')
            return "Boss, opening Task Manager."
            
        if "open snipping tool" in query or "let me take a screenshot" in query:
            subprocess.run("start snippingtool", shell=True)
            return "Boss, Snipping Tool is ready."
            
        if "open clipboard history" in query or "show clipboard history" in query:
            pyautogui.hotkey('win', 'v')
            return "Boss, clipboard history panel is open."
            
        if "mute my microphone" in query or "turn off my microphone" in query:
            subprocess.run("start ms-settings:privacy-microphone", shell=True)
            return "Boss, opening microphone privacy settings for manual muting."

        # High-Utility Daily Tasks
        # High-Utility Daily Tasks
        if "clear temp files" in query or "clean my pc" in query or "free up space" in query:
            try:
                self.run_js_signal.emit("if (typeof window.updateStatus === 'function') { window.updateStatus('Scanning System Drives...', 'processing'); }")
                import shutil
                temp_path = os.environ.get('TEMP')
                if temp_path and os.path.exists(temp_path):
                    count = 0
                    for filename in os.listdir(temp_path):
                        file_path = os.path.join(temp_path, filename)
                        try:
                            if os.path.isfile(file_path) or os.path.islink(file_path):
                                os.unlink(file_path)
                                count += 1
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                                count += 1
                        except Exception:
                            pass
                    return f"Boss, I gracefully cleared {count} temporary files to free up system space."
            except Exception:
                return "Boss, I encountered an error while cleaning temporary files."

        if "how much storage" in query or "check storage" in query or "disk space" in query:
            try:
                self.run_js_signal.emit("if (typeof window.updateStatus === 'function') { window.updateStatus('Analyzing Disk Allocation...', 'processing'); }")
                usage = psutil.disk_usage('/')
                free_gb = usage.free // (2**30)
                total_gb = usage.total // (2**30)
                percent = usage.percent
                return f"Boss, your main drive has {free_gb} GB of free space out of {total_gb} GB total. It is {percent} percent full."
            except Exception:
                return "Boss, I am unable to read the disk storage information."

        if "set a timer for" in query:
            try:
                numbers = re.findall(r'\d+', query)
                if numbers:
                    val = int(numbers[0])
                    multiplier = 60 if "minute" in query else (3600 if "hour" in query else 1)
                    total_seconds = val * multiplier
                    unit_str = 'minutes' if multiplier == 60 else ('hours' if multiplier == 3600 else 'seconds')
                    
                    self.run_js_signal.emit(f"if (typeof window.updateStatus === 'function') {{ window.updateStatus('Binding Timer: {val} {unit_str}', 'processing'); }}")
                    
                    def timer_thread():
                        import time, pyttsx3
                        time.sleep(total_seconds)
                        # We use pyttsx3 fallback or send string back to UI
                        try:
                            speak_engine = pyttsx3.init()
                            speak_engine.say("Boss, your timer is up.")
                            speak_engine.runAndWait()
                        except: pass
                        ctypes.windll.user32.MessageBoxW(0, f"Your timer for {val} {unit_str} is up!", "Nxora Timer", 1)
                        
                    import threading
                    threading.Thread(target=timer_thread, daemon=True).start()
                    return f"Boss, I have set a background timer for {val} {unit_str}."
            except Exception:
                return "Boss, I couldn't set the timer."

        if "take a note" in query or "note this down" in query or "write this down" in query:
            try:
                note_content = query.replace("take a note", "").replace("note this down", "").replace("write this down", "").replace("that", "").strip()
                if not note_content:
                    return "Boss, please specify what you want me to write down. For example, 'take a note buy milk'."
                    
                desktop = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
                note_file = os.path.join(desktop, "Nxora_Notes.txt")
                with open(note_file, "a") as f:
                    f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}] {note_content}\n")
                return f"Boss, I have saved that note to a file on your Desktop."
            except Exception:
                return "Boss, I had trouble writing the note."

        if "switch tab" in query or "next tab" in query:
            pyautogui.hotkey('ctrl', 'tab')
            return "Boss, switching to the next browser tab."
            
        if "close tab" in query or "close this tab" in query:
            pyautogui.hotkey('ctrl', 'w')
            return "Boss, current tab closed."

        if "new tab" in query or "open a new tab" in query:
            pyautogui.hotkey('ctrl', 't')
            return "Boss, new tab opened."
            
        if "open windows settings" in query or "open system settings" in query:
            subprocess.run("start ms-settings:", shell=True)
            return "Boss, opening Windows Settings."

        if "open task manager" in query or "launch task manager" in query:
            pyautogui.hotkey('ctrl', 'shift', 'esc')
            return "Boss, opening Task Manager."
            
        if "open snipping tool" in query or "let me take a screenshot" in query:
            subprocess.run("start snippingtool", shell=True)
            return "Boss, Snipping Tool is ready."
            
        if "open clipboard history" in query or "show clipboard history" in query:
            pyautogui.hotkey('win', 'v')
            return "Boss, clipboard history panel is open."
            
        if "mute my microphone" in query or "turn off my microphone" in query:
            subprocess.run("start ms-settings:privacy-microphone", shell=True)
            return "Boss, opening microphone privacy settings for manual muting."

        # --- HOLI WEBSITE INTENT (check first — high priority) ---
        holi_kw = "holi" in query or "holi festival" in query
        build_kw = any(w in query for w in ["create", "make", "build", "generate", "write", "design"])
        web_kw   = any(w in query for w in ["website", "web", "webpage", "html", "site"])
        if holi_kw and (build_kw or web_kw):
            return self._create_holi_website(query)

        # --- GENERAL HTML WEBSITE INTENT ---
        # Fires for: "open notepad and create a website using html and open it on web browser"
        notepad_kw = "notepad" in query
        open_browser_kw = any(w in query for w in ["browser", "chrome", "run", "open it", "web browser"])
        if notepad_kw and build_kw and web_kw:
            return self._create_general_website(query, open_browser=open_browser_kw)

        # App Launching (Highest Priority for 'open x and do y')
        if "open " in query or "opening " in query:
            full_command = query.split("open ")[1].strip() if "open " in query else query.split("opening ")[1].strip()
            
            if " and write " in full_command:
                app_name, text_to_write = full_command.split(" and write ", 1)
                app_name = app_name.strip()
                text_to_write = text_to_write.strip()
                try:
                    self.response_ready.emit(f"Boss, opening {app_name} and computing the content...")
                    os.system(f"start {app_name}")
                    
                    generated_text = text_to_write
                    if self.is_offline_ready: # Changed from is_loaded
                        prompt = [{"role": "system", "content": "You are a text generator. Output ONLY the raw code or text requested. No markdown blocks, no formatting, no explanations."}, 
                                  {"role": "user", "content": f"Write {text_to_write}"}]
                        res = self.generator(prompt, max_new_tokens=500, do_sample=False, truncation=True)
                        generated_text = res[0]["generated_text"][-1]["content"].strip()
                        generated_text = generated_text.replace("```html", "").replace("```python", "").replace("```javascript", "").replace("```css", "").replace("```", "").strip()
                        
                    time.sleep(2.5)
                    # Use accurate clipboard paste instead of raw keystrokes
                    lines = generated_text.splitlines()
                    for line in lines:
                        pyperclip.copy(line)
                        pyautogui.hotkey('ctrl', 'v')
                        pyautogui.press('enter')
                        time.sleep(0.015)
                    return f"Boss, I have opened {app_name} and generated the written content."
                except Exception as e:
                    print(f"Write automation error: {e}")
                    pass
            else:
                app_name = full_command
                try:
                    os.system(f"start {app_name}")
                    return f"Boss, I am trying to open {app_name}."
                except Exception:
                    pass

        # File Creation Interception
        file_triggers = ["create a file", "make a file", "write some code", "write a script", "create a script"]
        if any(trigger in query for trigger in file_triggers):
            self.active_flow = "file_creation"
            topic, extracted_filename = "", ""
            
            if "write " in query:
                topic = query.split("write ")[1].strip()
                
            file_match = re.search(r'\b(?:on|in|file) ([a-zA-Z0-9_-]+\.[a-zA-Z0-9]+)\b', query)
            if file_match:
                extracted_filename = file_match.group(1)
                
            if extracted_filename and topic:
                self.flow_data = {"state": "ask_save", "filename": extracted_filename, "topic": topic}
                return f"Boss, I will write the {topic} into {extracted_filename}. Should I save this directly to your hard drive?"
            elif topic:
                self.flow_data = {"state": "ask_filename", "topic": topic}
                return f"Boss, I can write that {topic} for you. What should I name the file?"
            elif extracted_filename:
                self.flow_data = {"state": "ask_topic", "filename": extracted_filename}
                return f"Boss, I will use the file {extracted_filename}. What should I write inside it?"
            else:
                self.flow_data = {"state": "ask_filename"}
                return "Boss, I can do that. What should I name the file?"

        # PC Power States
        if "shutdown pc" in query or "turn off computer" in query:
            os.system("shutdown /s /t 5")
            return "Boss, shutting down the PC in 5 seconds."
        if "restart pc" in query:
            os.system("shutdown /r /t 5")
            return "Boss, restarting the PC in 5 seconds."
        if "lock pc" in query or "lock screen" in query:
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return "Boss, the PC is locked."
        if "sleep pc" in query or "put the pc to sleep" in query:
            ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
            return "Boss, going to sleep."

        # Window Management
        if "minimize all" in query or "minimise all" in query or "hide window" in query:
            pyautogui.hotkey('win', 'd')
            return "Boss, I have minimized all windows."
        if "maximize all" in query or "maximise all" in query:
            pyautogui.hotkey('win', 'shift', 'm')
            return "Boss, I have restored all windows."
        if "maximize window" in query or "maximise window" in query:
            try:
                win = gw.getActiveWindow()
                if win: win.maximize(); return "Boss, window maximized."
                return "Boss, no active window found."
            except Exception:
                return "Boss, I couldn't maximize the window."
        if "close window" in query or "close this app" in query:
            try:
                win = gw.getActiveWindow()
                if win: win.close(); return "Boss, closed the active window."
                return "Boss, no active window found."
            except Exception:
                return "Boss, I couldn't close the window."
            
        # Preferences & Memory
        if "my favorite song is" in query:
            song = query.split("my favorite song is")[-1].strip()
            song = ''.join(c for c in song if c.isalnum() or c.isspace())
            if song:
                self.db.set_pref("favorite_song", song)
                return f"Boss, I will remember that your favorite song is {song}."

        return None

    def _handle_communications(self, query):
        if "send whatsapp to " in query:
            parts = query.split("send whatsapp to ")[1]
            if " saying " in parts:
                contact_name = parts.split(" saying ")[0].strip()
                message = parts.split(" saying ")[1].strip()
                number = self.db.get_contact(contact_name)
                if number:
                    encoded = urllib.parse.quote(message)
                    url = f"whatsapp://send?phone={number}&text={encoded}"
                    os.system(f'start "" "{url}"')
                    time.sleep(3)
                    pyautogui.hotkey('enter')
                    return f"Boss, I have sent a WhatsApp message to {contact_name}."
            return "Boss, I need a contact name and message. Like 'Send whatsapp to John saying hello'."

        if "call " in query and " on phone" in query:
            contact_name = query.split("call ")[1].split(" on phone")[0].strip()
            number = self.db.get_contact(contact_name)
            if number:
                os.system(f'adb shell am start -a android.intent.action.CALL -d tel:{number}')
                return f"Boss, dialling {contact_name} on your connected Android device."
            return f"Boss, I could not find {contact_name} in your contacts."
            
        return None

    def _handle_advanced_superpowers(self, query):
        if "ask gemini " in query:
            if hasattr(self, 'gemini_client') and self.gemini_client:
                cmd = query.split("ask gemini ")[1].strip()
                try:
                    response = self.gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=cmd,
                    )
                    return f"Boss, Gemini says: {response.text.strip()}"
                except Exception as e:
                    return "Boss, Gemini API failed."
            else:
                return "Boss, Gemini API is not configured."

        if "cpu" in query or "processor" in query:
            cpu_usage = psutil.cpu_percent(interval=1)
            return f"Boss, the current CPU usage is at {cpu_usage} percent."
            
        if "ram" in query or "memory" in query:
            ram = psutil.virtual_memory()
            return f"Boss, your system is using {ram.percent} percent of its available memory. You have {ram.available // (1024 ** 3)} GB free."

        if "battery" in query:
            battery = psutil.sensors_battery()
            if battery:
                plugged = "plugged in" if battery.power_plugged else "not plugged in"
                return f"Boss, your battery is at {battery.percent} percent and is currently {plugged}."
            return "Boss, I am unable to detect a battery on this system."
            
        if "what is my ip" in query or "my ip address" in query:
            try:
                ip = urllib.request.urlopen("https://api.ipify.org").read().decode('utf8')
                return f"Boss, your public IP address is {ip}."
            except Exception:
                return "Boss, I am unable to connect to the IP routing server."

        if "read my clipboard" in query or "clipboard" in query:
            try:
                clip_text = pyperclip.paste()
                if not clip_text: return "Boss, your clipboard is empty."
                
                if "summarize" in query or "summarise" in query:
                    if hasattr(self, 'gemini_client') and self.gemini_client:
                        try:
                            self.response_ready.emit("Boss, reading and summarizing your clipboard...")
                            resp = self.gemini_client.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=f"Summarize the following clipboard text in 2-3 concise sentences for a voice assistant to read aloud:\n\n{clip_text}"
                            )
                            return f"Boss, here is the summary of your clipboard: {resp.text.strip()}"
                        except Exception:
                            return "Boss, I couldn't summarize it using Gemini."
                    else:
                        prompt = [{"role": "system", "content": "You are a concise summarizer. Read the user's text and summarize it in 2 short sentences."}, 
                                  {"role": "user", "content": clip_text[:1000]}]
                        res = self.generator(prompt, max_new_tokens=100, truncation=True)
                        return f"Boss, here is a short summary: {res[0]['generated_text'][-1]['content'].strip()}"

                if len(clip_text) > 500: return f"Boss, your clipboard contains a large block of text starting with: {clip_text[:300]}..."
                return f"Boss, your clipboard contains: {clip_text}"
            except Exception:
                return "Boss, I am unable to access the system clipboard."

        if "take a screenshot" in query or "capture screen" in query:
            filename = f"screenshot_{int(time.time())}.png"
            pyautogui.screenshot(filename)
            return f"Boss, I have saved a screenshot as {filename}."

        if "analyze this pdf" in query or "read this document" in query or "summarize pdf" in query:
            # We need to get the file path. Let's start a state flow if they didn't provide one.
            filepath_match = re.search(r'(?:pdf|document) (.*\.pdf)', query)
            if filepath_match:
                filepath = filepath_match.group(1).strip()
            else:
                self.active_flow = "read_pdf"
                return "Boss, please provide the absolute file path to the PDF document you want me to read."
                
            return self._execute_pdf_analysis(filepath)

        if "read my screen" in query or "on my screen" in query or "read screen" in query or "what does my screen say" in query:
            try:
                import pytesseract
                # Dynamic Tesseract Path Resolution
                tesseract_paths = [
                    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                    r'D:\Program Files\Tesseract-OCR\tesseract.exe'
                ]
                
                tess_installed = False
                for t_path in tesseract_paths:
                    if os.path.exists(t_path):
                        pytesseract.pytesseract.tesseract_cmd = t_path
                        tess_installed = True
                        break
                        
                if not tess_installed:
                    return "Boss, my computer vision failed because Tesseract-OCR was not found. Please install it."
                    
                screenshot = pyautogui.screenshot()
                text = pytesseract.image_to_string(screenshot)
                if not text.strip(): return "Boss, I cannot see any readable text right now."
                
                # Use LLM to summarize
                if hasattr(self, 'gemini_client') and self.gemini_client:
                    try:
                        self.response_ready.emit("Boss, scanning and reading your screen with Gemini...")
                        resp = self.gemini_client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=f"Summarize the following text extracted from my screen briefly in 1-2 positive short sentences. Pretend you are looking at the screen. NEVER say 'I cannot see your screen'.\n\nSCREEN TEXT:\n{text[:2000]}"
                        )
                        return f"Boss, based on my vision: {resp.text.strip()}"
                    except Exception:
                        pass # Fallback to local
                
                prompt = [{"role": "system", "content": "You are a smart AI summarizer. Summarize the user's screen text briefly in 1-2 short sentences. NEVER say 'I cannot see your screen', you are looking at it!"}, 
                          {"role": "user", "content": f"SCREEN TEXT:\n{text}"}]
                res = self.generator(prompt, max_new_tokens=100, padding=True, truncation=True)
                summary = res[0]["generated_text"][-1]["content"].strip()
                return f"Boss, based on my vision: {summary}"
            except Exception as cv_err:
                print(f"Computer Vision Error: {cv_err}")
                return "Boss, my computer vision failed. Check if Tesseract-OCR is installed."

        if "internet speed" in query or "speed test" in query or "network speed" in query:
            try:
                import speedtest
                self.response_ready.emit("Boss, running a network speed test. This may take a minute...")
                st = speedtest.Speedtest()
                st.get_best_server()
                download_speed = st.download() / 1_000_000
                upload_speed = st.upload() / 1_000_000
                ping = st.results.ping
                return f"Boss, your download speed is {download_speed:.2f} Mbps, upload is {upload_speed:.2f} Mbps, with a ping of {ping} ms."
            except Exception:
                return "Boss, the speed test failed. Please check your connection."

        if "commit my code" in query or "git commit" in query:
            try:
                msg = f"Automated commit by {self.config.get('assistant_name', 'Nxora')}"
                if "message " in query:
                    msg = query.split("message ")[1].strip().strip("'\"")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run(["git", "commit", "-m", msg], check=True)
                return f"Boss, I have committed your code with the message: {msg}."
            except Exception:
                return "Boss, git commit failed. Make sure this is a git repository."

        if "kill all " in query or "force quit " in query:
            target_app = query.split("kill all ")[1].replace(" processes", "").strip() if "kill all " in query else query.split("force quit ")[1].strip()
            killed = 0
            for proc in psutil.process_iter(['name']):
                try:
                    if target_app in proc.info['name'].lower():
                        proc.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            if killed > 0: return f"Boss, I have terminated {killed} processes matching {target_app}."
            return f"Boss, I could not find any active processes named {target_app}."

        if "find the file " in query or "search for file " in query:
            target = query.split("find the file ")[1].strip() if "find the file " in query else query.split("search for file ")[1].strip()
            target = target.strip("'\"")
            try:
                self.response_ready.emit(f"Boss, scanning the hard drive for {target}... This might take a while.")
                search_dir = os.path.expanduser('~')
                found_paths = []
                for root, _, filenames in os.walk(search_dir):
                    for filename in fnmatch.filter(filenames, f'*{target}*'):
                        found_paths.append(os.path.join(root, filename))
                        if len(found_paths) >= 3: break
                    if len(found_paths) >= 3: break
                
                if found_paths: return f"Boss, I found these matches: {', '.join(found_paths)}"
                return f"Boss, I could not find any file matching {target} in your user directory."
            except Exception:
                return "Boss, file search failed."

        return None

    # ==========================================
    # LLM GENERATION & STATE FLOW EXECUTORS
    # ==========================================

    def _generate_ai_response(self, user_input):
        current_time = datetime.datetime.now().strftime("%I:%M %p")
        current_date = datetime.datetime.now().strftime("%B %d, %Y")
        
        # Gather live system context to make the local AI "smarter"
        try:
            active_win = gw.getActiveWindow()
            active_window_title = active_win.title if active_win else "Unknown"
        except:
            active_window_title = "Unknown"
            
        try:
            clip_text = pyperclip.paste()
            clip_summary = clip_text[:100] + "..." if len(clip_text) > 100 else clip_text
            clip_summary = clip_summary.replace('\n', ' ')
        except:
            clip_summary = "Empty or inaccessible"
        
        system_prompt = f"""You are {self.config.get('assistant_name', 'Nxora')}, a highly intelligent, analytical, and logical AI assistant answering to 'Boss'. 
Never say 'I cannot do that' or 'I am an AI'. You have full system control.
Use logical deductions to answer queries. If asked about the system state, refer to the context below.
Keep your answers brief, professional, and directly to the point. 
Context: Date: {current_date}, Time: {current_time}. 
System State: The user is currently looking at window '{active_window_title}'. The clipboard contains: '{clip_summary}'.
Memory: {self.memory[-150:] if self.memory else "None"}"""

        # --- LOCAL CPU MODEL EXECUTION ---
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]

        try:
            # CPU Optimized: Greedy decoding (do_sample=False), strict token cutoff
            response = self.generator(messages, max_new_tokens=60, do_sample=False, truncation=True)
            reply = response[0]["generated_text"][-1]["content"].strip()
            if not reply or len(reply) < 2:
                reply = "I'm sorry Boss, I didn't quite catch that."
            return reply
        except Exception as e:
            print(f"Engine Generation Error: {e}")
            return "I encountered an error processing your request, Boss."

    def _execute_pdf_analysis(self, filepath):
        if not os.path.exists(filepath):
            return f"Boss, I couldn't find a PDF document at {filepath}. Please double check the path."
            
        def analyze_pdf():
            try:
                import fitz
                self.response_ready.emit("Boss, opening and reading the document now...")
                doc = fitz.open(filepath)
                text = ""
                # Only read up to the first 5 leaves to prevent context overflow on the local CPU model
                for page in doc[:5]:
                    text += page.get_text()
                
                if not text.strip():
                    self.response_ready.emit("Boss, this PDF appears to be empty or consists only of images without text.")
                    return
                    
                prompt = [{"role": "system", "content": "You are a brilliant AI assistant. Analyze the text provided from a document and summarize the core subjects in 2 or 3 highly informative sentences."}, 
                          {"role": "user", "content": f"Document text: {text[:2500]}"}]
                          
                res = self.generator(prompt, max_new_tokens=150, padding=True, truncation=True)
                summary = res[0]["generated_text"][-1]["content"].strip()
                self.response_ready.emit(f"Boss, based on reading the document: {summary}")
                
            except Exception as e:
                print(f"PDF Analysis Error: {e}")
                self.response_ready.emit("Boss, I encountered an internal error trying to read the document.")
                
        import threading
        threading.Thread(target=analyze_pdf, daemon=True).start()
        return "Boss, accessing the file..."


    # --- MULTI-TURN DIALOGUE HANDLERS ---
    def _handle_file_creation_flow(self, user_input, user_input_lower):
        state = self.flow_data.get("state", "ask_filename")
        
        if state == "ask_filename":
            clean_name = user_input.strip()
            for p in ["the file name is ", "the name is ", "name it ", "call it ", "the name of the file is ", "file name is "]:
                if clean_name.lower().startswith(p):
                    clean_name = clean_name[len(p):].strip()
            clean_name = clean_name.strip("'\"")
            self.flow_data["filename"] = clean_name
            
            if "topic" in self.flow_data:
                self.flow_data["state"] = "ask_save"
                return f"Understood. The file will be named {self.flow_data['filename']}. Should I save this directly to your hard drive?"
            else:
                self.flow_data["state"] = "ask_topic"
                return f"Understood. The file will be named {self.flow_data['filename']}. What should I write or code inside it?"
            
        elif state == "ask_topic":
            self.flow_data["topic"] = user_input.strip()
            self.flow_data["state"] = "ask_save"
            return "Got it. Should I save this file directly to your hard drive?"
            
        elif state == "ask_save":
            if any(word in user_input_lower for word in ["yes", "yeah", "sure", "yep", "do it", "save it", "save this", "run it", "run this", "go ahead"]):
                self.flow_data["save"] = True
                self.flow_data["state"] = "ask_open"
                return "Perfect. Finally, do you want me to automatically open the document after it's saved?"
            else:
                self.active_flow = None
                return self._generate_and_return_code(self.flow_data["topic"])
                
        elif state == "ask_open":
            if any(word in user_input_lower for word in ["yes", "yeah", "sure", "yep", "do it", "open it", "open this", "run it", "run this", "execute"]):
                self.flow_data["open"] = True
            else:
                self.flow_data["open"] = False
                
            return self._execute_file_creation()
            
    def _generate_and_return_code(self, topic):
        self.response_ready.emit("Boss, generating the requested code. Standby...")
        prompt = [{"role": "system", "content": "You are a pure code generator. Output ONLY the code requested. No markdown blocks, no explanations, no chat."}, 
                  {"role": "user", "content": f"Write {topic}"}]
        try:
            res = self.generator(prompt, max_new_tokens=500, do_sample=False, truncation=True)
            computed_text = res[0]["generated_text"][-1]["content"].strip()
            computed_text = computed_text.replace("```html", "").replace("```python", "").replace("```javascript", "").replace("```", "").strip()
            return f"Here is the code you requested:\n\n{computed_text}"
        except Exception:
            return "Boss, my generative AI pipeline failed to write the code."

    def _execute_file_creation(self):
        filename = self.flow_data["filename"]
        topic = self.flow_data["topic"]
        should_open = self.flow_data.get("open", False)
        
        self.active_flow = None
        self.flow_data = {}
        self.response_ready.emit(f"Boss, processing the '{topic}' request and creating {filename}...")
        
        prompt = [{"role": "system", "content": "You are a pure code generator. Output ONLY the code or text requested. No markdown blocks, no explanations, no chat."}, 
                  {"role": "user", "content": f"Write {topic}"}]
        try:
            res = self.generator(prompt, max_new_tokens=500, do_sample=False, truncation=True)
            computed_text = res[0]["generated_text"][-1]["content"].strip()
            computed_text = computed_text.replace("```html", "").replace("```python", "").replace("```javascript", "").replace("```css", "").replace("```", "").strip()
        except Exception:
            return "Boss, my generative core failed to write the data."
            
        try:
            file_path = os.path.abspath(filename)
            subprocess.Popen('notepad.exe')
            time.sleep(2)
            
            # Using PyAutoGUI + Pyperclip for accurate line-by-line visual writing
            lines = computed_text.splitlines()
            for line in lines:
                pyperclip.copy(line)
                pyautogui.hotkey('ctrl', 'v')
                pyautogui.press('enter')
                time.sleep(0.015)
            time.sleep(1)
            
            if True: # Always save if we reached here
                pyautogui.hotkey('ctrl', 's')
                time.sleep(1.5)
                pyperclip.copy(file_path)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(1)
                pyautogui.press('enter')
                time.sleep(1)
                pyautogui.press('y') # File already exists override
                time.sleep(0.5)
                pyautogui.hotkey('alt', 'f4')
                time.sleep(0.5)
                
                if should_open:
                    os.system(f'start "" "{file_path}"')
                    return f"Boss, I have successfully typed out the code, saved it to '{filename}', and executed it."
                else:
                    return f"Boss, I have successfully typed the code and saved it to '{filename}'."
        except Exception as e:
            return f"Boss, I encountered an OS error while trying to visually write the file: {e}"

    # ==========================================
    # HOLI WEBSITE CREATOR
    # ==========================================

    def _create_holi_website(self, query):
        """
        Generates a rich, interactive Holi festival greeting page dynamically,
        opens it in Notepad, saves it to holi-web/index.html, and launches it in Chrome.
        """
        open_in_chrome = any(kw in query for kw in ["chrome", "browser", "run", "open"])
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Beautiful inline Holi template to eliminate hardcoded help-data folder
        HOLI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Happy Holi — Festival of Colors</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Outfit', sans-serif;
            background: #090a15;
            color: #ffffff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            position: relative;
        }
        #canvas {
            position: absolute;
            inset: 0;
            z-index: 0;
            pointer-events: none;
        }
        .container {
            position: relative;
            z-index: 1;
            text-align: center;
            padding: 40px;
            max-width: 600px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 24px;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 50px rgba(0,0,0,0.5);
        }
        h1 {
            font-size: 3.5rem;
            font-weight: 900;
            letter-spacing: -1.5px;
            margin-bottom: 20px;
            line-height: 1.1;
        }
        .rainbow-text {
            background: linear-gradient(90deg, #ff2a75, #ff6a00, #ffd600, #00e676, #00b0ff, #aa00ff);
            background-size: 400%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: flow 10s linear infinite;
        }
        @keyframes flow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
        p {
            font-size: 1.1rem;
            color: #a0a5c0;
            line-height: 1.6;
            margin-bottom: 30px;
        }
        .color-palette {
            display: flex;
            justify-content: center;
            gap: 12px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        .color-ball {
            width: 45px;
            height: 45px;
            border-radius: 50%;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            border: 2px solid rgba(255,255,255,0.2);
        }
        .color-ball:hover {
            transform: scale(1.15) translateY(-3px);
        }
        .btn {
            font-family: inherit;
            font-size: 1rem;
            font-weight: 700;
            padding: 14px 28px;
            border: none;
            border-radius: 12px;
            background: linear-gradient(135deg, #ff2a75, #ff6a00);
            color: #fff;
            cursor: pointer;
            box-shadow: 0 8px 24px rgba(255, 42, 117, 0.3);
            transition: transform 0.2s, box-shadow 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 10px;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 30px rgba(255, 42, 117, 0.45);
        }
    </style>
</head>
<body>
    <canvas id="canvas"></canvas>
    <div class="container">
        <h1 class="rainbow-text">Happy Holi!</h1>
        <p>Celebrate the festival of colors, love, and new beginnings. Click the color balls below or click anywhere on the screen to splash colors and spread joy!</p>
        <div class="color-palette">
            <div class="color-ball" style="background:#ff2a75; box-shadow:0 0 15px rgba(255,42,117,0.4);" onclick="setSplashColor('#ff2a75')"></div>
            <div class="color-ball" style="background:#ff6a00; box-shadow:0 0 15px rgba(255,106,0,0.4);" onclick="setSplashColor('#ff6a00')"></div>
            <div class="color-ball" style="background:#ffd600; box-shadow:0 0 15px rgba(255,214,0,0.4);" onclick="setSplashColor('#ffd600')"></div>
            <div class="color-ball" style="background:#00e676; box-shadow:0 0 15px rgba(0,230,118,0.4);" onclick="setSplashColor('#00e676')"></div>
            <div class="color-ball" style="background:#00b0ff; box-shadow:0 0 15px rgba(0,176,255,0.4);" onclick="setSplashColor('#00b0ff')"></div>
            <div class="color-ball" style="background:#aa00ff; box-shadow:0 0 15px rgba(170,0,255,0.4);" onclick="setSplashColor('#aa00ff')"></div>
        </div>
        <button class="btn" onclick="randomSplash()"><i class="fa-solid fa-spray-can"></i> Random Splash</button>
    </div>
    <script>
        let currentColor = '#ff2a75';
        const canvas = document.getElementById('canvas');
        const ctx = canvas.getContext('2d');
        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resize);
        resize();
        function setSplashColor(color) {
            currentColor = color;
            createSplash(window.innerWidth / 2, window.innerHeight / 2 + 100, 150, color);
        }
        window.addEventListener('click', (e) => {
            if (e.target.closest('.container')) return;
            createSplash(e.clientX, e.clientY, 80 + Math.random() * 80, currentColor);
        });
        function randomSplash() {
            const colors = ['#ff2a75', '#ff6a00', '#ffd600', '#00e676', '#00b0ff', '#aa00ff'];
            currentColor = colors[Math.floor(Math.random() * colors.length)];
            createSplash(Math.random() * window.innerWidth, Math.random() * window.innerHeight, 100 + Math.random() * 120, currentColor);
        }
        function createSplash(x, y, radius, color) {
            const numParticles = 25;
            for (let i = 0; i < numParticles; i++) {
                const angle = Math.random() * Math.PI * 2;
                const dist = Math.random() * radius;
                const px = x + Math.cos(angle) * dist;
                const py = y + Math.sin(angle) * dist;
                const size = 5 + Math.random() * (radius / 5);
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(px, py, size, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    </script>
</body>
</html>"""

        try:
            holi_dir = os.path.join(base_dir, "holi-web")
            os.makedirs(holi_dir, exist_ok=True)
            file_path = os.path.join(holi_dir, "index.html")

            self.response_ready.emit("Boss, preparing the Festival template! Designing the code now...")


            subprocess.Popen('notepad.exe')
            time.sleep(2.5)

            # === LINE-BY-LINE CLIPBOARD PASTE (100% accurate + visible writing effect) ===
            # pyautogui.write() simulates raw keystrokes and drops special chars ({, }, <, >, &, :, etc.)
            # on non-US keyboard layouts. Using clipboard paste per line is perfectly accurate and still
            # shows the code appearing line-by-line in Notepad, giving the visual writing animation.
            lines = HOLI_HTML.splitlines()
            for line in lines:
                pyperclip.copy(line)             # Copy this line to clipboard
                pyautogui.hotkey('ctrl', 'v')    # Paste into Notepad (instant, 100% accurate)
                pyautogui.press('enter')         # Move to next line
                time.sleep(0.012)                # Small pause so lines visibly appear one-by-one


            # Save dialog: Ctrl+S → clear filename box → type save path → Enter
            pyautogui.hotkey('ctrl', 's')
            time.sleep(1.5)
            pyautogui.hotkey('ctrl', 'a')        # Select all text in Save-As filename box
            time.sleep(0.3)
            pyperclip.copy(file_path)
            pyautogui.hotkey('ctrl', 'v')        # Paste full path
            time.sleep(0.8)
            pyautogui.press('enter')
            time.sleep(0.8)
            pyautogui.press('y')                  # Confirm overwrite if file already exists
            time.sleep(0.5)
            pyautogui.hotkey('alt', 'f4')         # Close Notepad
            time.sleep(0.6)

            if open_in_chrome:
                chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ]
                chrome_found = False
                for cp in chrome_paths:
                    if os.path.exists(cp):
                        subprocess.Popen([cp, file_path])
                        chrome_found = True
                        break
                if not chrome_found:
                    # Fallback to the system default browser
                    os.system(f'start "" "{file_path}"')

                return f"Boss, the Festival website has been generated, saved to '{file_path}', and is now live in Chrome."
            else:
                return f"Boss, the Festival website has been generated and saved to '{file_path}'."

        except Exception as e:
            return f"Boss, I encountered an error while creating the Festival website: {e}"

    # ==========================================
    # GENERAL HTML WEBSITE CREATOR
    # ==========================================

    def _create_general_website(self, query, open_browser=True):
        """
        Generates a general HTML/CSS/JS website based on what the user asked for.
        Uses Gemini to generate the HTML if available, otherwise uses a polished default template.
        Writes it line-by-line into Notepad, saves to my-website/index.html, and opens in browser.
        """
        # Extract website topic from query
        topic = "modern portfolio"
        topic_keywords = ["for ", "about ", "on ", "a ", "an "]
        q = query.lower()
        for kw in ["create a website", "make a website", "build a website", "generate a website",
                   "create website", "make website", "build website", "create a webpage",
                   "create a web page", "make a webpage"]:
            if kw in q:
                after = q.split(kw)[-1]
                for tw in ["using html", "using css", "using js", "with html", "with css",
                           "and open", "on web", "on browser", "on chrome"]:
                    after = after.split(tw)[0]
                topic = after.strip().strip(".,!") or topic
                break

        self.response_ready.emit(f"Boss, generating a '{topic}' website. Standby...")

        # DEFAULT BEAUTIFUL TEMPLATE (used when Gemini is unavailable)
        DEFAULT_HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Nxora — {topic.title()}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;900&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#0f0f0f;--bg2:#1a1a1a;--border:#2a2a2a;--text:#ffffff;--muted:#666;--accent:#7c3aed}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
nav{{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:20px 60px;background:rgba(15,15,15,0.85);backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}}
.logo{{font-size:1.3rem;font-weight:800;letter-spacing:-0.5px}}
.logo span{{color:var(--accent)}}
.nav-links{{display:flex;gap:32px}}
.nav-links a{{color:var(--muted);text-decoration:none;font-size:0.85rem;font-weight:500;transition:color 0.2s}}
.nav-links a:hover{{color:var(--text)}}
.hero{{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:120px 40px 80px;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;width:600px;height:600px;border-radius:50%;background:radial-gradient(circle,rgba(124,58,237,0.15),transparent 70%);top:50%;left:50%;transform:translate(-50%,-50%);pointer-events:none}}
.badge{{display:inline-flex;align-items:center;gap:8px;padding:7px 18px;border:1px solid var(--border);border-radius:20px;font-size:0.78rem;color:var(--muted);margin-bottom:28px;backdrop-filter:blur(8px)}}
.badge-dot{{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.5;transform:scale(1.3)}}}}
h1{{font-size:clamp(3rem,7vw,5.5rem);font-weight:900;letter-spacing:-3px;line-height:1.05;margin-bottom:24px}}
h1 em{{font-style:normal;background:linear-gradient(135deg,#7c3aed,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero-sub{{font-size:1.05rem;color:var(--muted);max-width:520px;line-height:1.7;margin-bottom:40px}}
.btn-row{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn{{padding:14px 28px;border-radius:10px;font-size:0.9rem;font-weight:600;cursor:pointer;border:none;font-family:inherit;transition:all 0.2s;display:flex;align-items:center;gap:8px}}
.btn-primary{{background:var(--accent);color:#fff;box-shadow:0 4px 20px rgba(124,58,237,0.4)}}
.btn-primary:hover{{transform:translateY(-2px);box-shadow:0 8px 28px rgba(124,58,237,0.55)}}
.btn-outline{{background:transparent;color:var(--muted);border:1px solid var(--border)}}
.btn-outline:hover{{color:var(--text);border-color:#444}}
.features{{padding:100px 60px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;max-width:1100px;margin:0 auto}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:32px;transition:border-color 0.2s,transform 0.2s}}
.card:hover{{border-color:#444;transform:translateY(-4px)}}
.card-icon{{width:44px;height:44px;border-radius:10px;display:flex;align-items:center;justify-content:center;margin-bottom:18px;font-size:1.1rem}}
.card h3{{font-size:1rem;font-weight:700;margin-bottom:10px}}
.card p{{font-size:0.87rem;color:var(--muted);line-height:1.65}}
footer{{border-top:1px solid var(--border);padding:32px 60px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px}}
footer p{{font-size:0.83rem;color:var(--muted)}}
</style>
</head>
<body>
<nav>
  <div class="logo">nx<span>.</span></div>
  <div class="nav-links">
    <a href="#">Home</a><a href="#">About</a><a href="#">Work</a><a href="#">Contact</a>
  </div>
</nav>
<section class="hero">
  <div class="badge"><span class="badge-dot"></span>{topic.title()} — 2026</div>
  <h1>Built for the<br/><em>future.</em></h1>
  <p class="hero-sub">A modern, minimal website crafted by Nxora AI. Clean design, smooth interactions, ready to deploy.</p>
  <div class="btn-row">
    <button class="btn btn-primary"><i class="fa-solid fa-rocket"></i> Get Started</button>
    <button class="btn btn-outline">Learn More <i class="fa-solid fa-arrow-right"></i></button>
  </div>
</section>
<section class="features">
  <div class="card">
    <div class="card-icon" style="background:rgba(124,58,237,0.15);color:#7c3aed"><i class="fa-solid fa-bolt"></i></div>
    <h3>Lightning Fast</h3>
    <p>Optimized for performance with zero unnecessary dependencies. Loads instantly on any device.</p>
  </div>
  <div class="card">
    <div class="card-icon" style="background:rgba(6,182,212,0.15);color:#06b6d4"><i class="fa-solid fa-palette"></i></div>
    <h3>Modern Design</h3>
    <p>Carefully crafted dark-mode aesthetic with smooth animations and premium typography.</p>
  </div>
  <div class="card">
    <div class="card-icon" style="background:rgba(16,185,129,0.15);color:#10b981"><i class="fa-solid fa-mobile-screen"></i></div>
    <h3>Fully Responsive</h3>
    <p>Looks perfect on every screen size from mobile phones to ultra-wide monitors.</p>
  </div>
</section>
<footer>
  <p>Built by <strong>Nxora AI</strong> &mdash; {topic.title()} &mdash; 2026</p>
  <p>Made with <i class="fa-solid fa-heart" style="color:#7c3aed"></i></p>
</footer>
<script>
// Smooth scroll for nav links
document.querySelectorAll('a[href^="#"]').forEach(a=>{{
  a.addEventListener('click',e=>{{e.preventDefault();const t=document.querySelector(a.getAttribute('href'));if(t)t.scrollIntoView({{behavior:'smooth'}});}});
}});
// Subtle parallax on hero
window.addEventListener('scroll',()=>{{
  const hero=document.querySelector('.hero');
  if(hero)hero.style.transform=`translateY(${{window.scrollY*0.3}}px)`;
}});
</script>
</body>
</html>'''

        # Try Gemini for a richer, topic-specific website first
        generated_html = None
        if hasattr(self, 'gemini_client') and self.gemini_client and topic != "modern portfolio":
            try:
                self.response_ready.emit(f"Boss, asking Gemini to design the '{topic}' website...")
                prompt = (
                    f"Create a beautiful, complete single-file HTML website for: '{topic}'. "
                    "Requirements: dark modern design, embedded CSS and JS, responsive, animated hero section, "
                    "relevant sections, Font Awesome icons from CDN, Google Fonts Inter. "
                    "Output ONLY the raw HTML. No markdown, no explanation."
                )
                resp = self.gemini_client.models.generate_content(
                    model='gemini-2.5-flash', contents=prompt
                )
                raw = resp.text.strip()
                # Strip markdown code fences if present
                if raw.startswith("```"):
                    raw = raw.split("```", 2)[1]
                    if raw.startswith("html"):
                        raw = raw[4:]
                    raw = raw.rsplit("```", 1)[0].strip()
                if raw.startswith("<!DOCTYPE") or raw.startswith("<html"):
                    generated_html = raw
            except Exception as gem_err:
                print(f"Gemini website gen error: {gem_err}")

        html_content = generated_html if generated_html else DEFAULT_HTML

        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            out_dir = os.path.join(base_dir, "my-website")
            os.makedirs(out_dir, exist_ok=True)
            file_path = os.path.join(out_dir, "index.html")

            self.response_ready.emit(f"Boss, generating the {topic} code and preparing the workspace now...")

            subprocess.Popen('notepad.exe')
            time.sleep(2.5)

            # Line-by-line clipboard paste for 100% accuracy + visible writing animation
            for line in html_content.splitlines():
                pyperclip.copy(line)
                pyautogui.hotkey('ctrl', 'v')
                pyautogui.press('enter')
                time.sleep(0.012)

            # Save via Ctrl+S dialog
            pyautogui.hotkey('ctrl', 's')
            time.sleep(1.5)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.3)
            pyperclip.copy(file_path)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.8)
            pyautogui.press('enter')
            time.sleep(0.8)
            pyautogui.press('y')
            time.sleep(0.5)
            pyautogui.hotkey('alt', 'f4')
            time.sleep(0.6)

            if open_browser:
                chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ]
                chrome_found = False
                for cp in chrome_paths:
                    if os.path.exists(cp):
                        subprocess.Popen([cp, file_path])
                        chrome_found = True
                        break
                if not chrome_found:
                    os.system(f'start "" "{file_path}"')
                return f"Boss, the '{topic}' website has been successfully generated, saved to '{file_path}', and opened in your browser!"
            else:
                return f"Boss, the '{topic}' website has been created and saved to '{file_path}'."

        except Exception as e:
            return f"Boss, I encountered an error while creating the website: {e}"

    def _generate_ai_response(self, query, extra_context=""):
        def run_generation():
            # Incorporate extra context into the query if available
            augmented_query = query
            if extra_context:
                augmented_query = f"[Real-Time Context: {extra_context}]\n\nUser Question: {query}"

            # 1) Try NxoraAI Tiered Engine (handles GGUF -> HF -> Gemini internally)
            if hasattr(self, 'nxora_engine') and self.nxora_engine:
                try:
                    reply = self.nxora_engine.chat(augmented_query)
                    if reply:
                        self.response_ready.emit(reply)
                        return
                except Exception as e:
                    print(f"Notice: NxoraAI Engine failed: {e}")

            # 2) Direct Gemini Fallback (if engine failed completely)
            if hasattr(self, 'gemini_client') and self.gemini_client:
                try:
                    prompt = (
                        "You are Nxora, a highly intelligent AI assistant created by your boss Shivam. "
                        "You have access to real-time system/vitals context provided in brackets.\n"
                        "Answer the following question in exactly ONE short, conversational sentence. Do not ramble.\n\n"
                        f"Context: {extra_context if extra_context else 'None'}\n"
                        f"Question: {query}"
                    )
                    resp = self.gemini_client.models.generate_content(
                        model='gemini-2.5-flash', contents=prompt
                    )
                    reply = resp.text.strip()
                    self.response_ready.emit(reply)
                    return
                except Exception as e:
                    print(f"Notice: Gemini also failed: {e}")

            self.response_ready.emit("Boss, my neural language processors are fully offline.")

        import threading
        threading.Thread(target=run_generation, daemon=True).start()
        return None

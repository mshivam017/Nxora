from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.engine import AIWorker
from core.database import MemoryDB

import json
from flask_sock import Sock
import pvporcupine
import struct
import base64
import tempfile
from pydub import AudioSegment

app = Flask(__name__)
CORS(app)
sock = Sock(app)

porcupine = None
try:
    porcupine = pvporcupine.create(
        access_key="DF0e72o7J/wbraSNgieuTqHzj5IDoyNf0MZvy86fOEEQZUIDzA+zxw==",
        keyword_paths=["../models/Next-Sora_en_windows_v4_0_0.ppn"]
    )
    print("Windows Wake Word Model Loaded for Mobile Streaming.")
except Exception as e:
    print(f"Failed to load Wake Word engine: {e}")

# Load Config dynamically like main.py
app_config = {"assistant_name": "Nxora"}
try:
    with open(os.path.join(os.path.dirname(__file__), '..', "config.json"), "r") as f:
        app_config = json.load(f)
except Exception as e:
    print(f"Warning: Could not load config.json in api.py: {e}")

assistant_name = app_config.get('assistant_name', 'Nxora')

# Initialize the Database and Engine globally for the API instance
db_path = os.path.join(os.path.dirname(__file__), '..', "data", f"{assistant_name}_memory.db")
db = MemoryDB(db_path)
bg_worker = AIWorker(db)

# We must load the model before accepting API traffic
print(f"Initializing {assistant_name} Engine Backend for Mobile API...")
bg_worker.load_model()
print("Mobile API Model Loaded Successfully.")

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "model_loaded": bg_worker.is_loaded}), 200

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "message": f"Welcome to the {assistant_name} API Backend.",
        "status": "online",
        "endpoints": ["/chat (POST)", "/health (GET)"]
    }), 200

@app.route('/chat', methods=['POST'])
def chat():
    if not bg_worker.is_loaded:
        return jsonify({"error": "Engine is still booting up."}), 503
        
    data = request.json
    if not data or 'message' not in data:
        return jsonify({"error": "Invalid request. Missing 'message' payload."}), 400
        
    user_message = data['message']
    
    # Process the message through the actual engine pipeline
    # We call process_input directly to cleanly return the string to the API
    try:
        reply = bg_worker.process_input(user_message)
        
        # Save to permanent memory database for persistence across Desktop + Mobile
        bg_worker.memory += f"User: {user_message} | {assistant_name}: {reply} "
        if len(bg_worker.memory) > 300:
            bg_worker.memory = bg_worker.memory[-300:]
            
        db.save_message("You", user_message)
        db.save_message(assistant_name, reply)
        
        return jsonify({"response": reply}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/chat_mobile', methods=['POST'])
def chat_mobile():
    if not bg_worker.is_loaded:
        return jsonify({"error": "Engine is still booting up."}), 503
        
    data = request.json
    if not data or 'message' not in data:
        return jsonify({"error": "Invalid request. Missing 'message' payload."}), 400
        
    user_message = data['message']
    
    # Bypass the Desktop OS execution layer, just use the pure LLM conversational engine
    try:
        reply = bg_worker._generate_ai_response(user_message)
        
        # Save to permanent memory database for persistence across Desktop + Mobile
        bg_worker.memory += f"User: {user_message} | {assistant_name}: {reply} "
        if len(bg_worker.memory) > 300:
            bg_worker.memory = bg_worker.memory[-300:]
            
        db.save_message("You", user_message)
        db.save_message(assistant_name, reply)
        
        return jsonify({"response": reply}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

import tempfile
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

@app.route('/transcribe', methods=['POST'])
def transcribe():
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file provided."}), 400

    audio_file = request.files['audio']
    
    try:
        # Save exact incoming stream (usually .m4a from Expo Android)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as temp_audio:
            audio_file.save(temp_audio.name)
            
        # Upload the audio file directly to Gemini for native transcription (No FFmpeg needed)
        uploaded_file = genai.upload_file(temp_audio.name)
        
        # Use fast flash model for STT
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content([
            "Listen to this audio and transcribe it exactly word for word. Do not add any extra commentary, just output the raw text spoken.",
            uploaded_file
        ])
        
        text = response.text.strip()
        
        # Cleanup
        os.unlink(temp_audio.name)
        uploaded_file.delete()

        return jsonify({"text": text}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@sock.route('/wake_word_stream')
def wake_word_stream(ws):
    if not porcupine:
        ws.send(json.dumps({"error": "Wake word engine unavailable."}))
        return
        
    frame_bytes = porcupine.frame_length * 2  # 16-bit PCM (2 bytes per sample)
    
    try:
        while True:
            data = ws.receive()
            if data is None: 
                break
                
            try:
                # Expo AV sends JSON with an m4a base64 string
                payload = json.loads(data)
                if 'audio_data' not in payload: continue
                
                chunk = base64.b64decode(payload['audio_data'])
                
                # Write to temp m4a file
                temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a")
                temp_audio.write(chunk)
                temp_audio.close()
                
                # Convert the M4A chunk into raw 16kHz PCM audio
                audio = AudioSegment.from_file(temp_audio.name, format="m4a")
                audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
                buf = audio.raw_data
                
                os.unlink(temp_audio.name)
                
                # Process frames identically to how PyPorcupine expects (512 samples)
                while len(buf) >= frame_bytes:
                    pcm_frame = buf[:frame_bytes]
                    buf = buf[frame_bytes:]
                    
                    pcm = struct.unpack_from("h" * porcupine.frame_length, pcm_frame)
                    keyword_index = porcupine.process(pcm)
                    
                    if keyword_index >= 0:
                        ws.send(json.dumps({"wake_word": True}))
                        break # Skip the rest of this 1s chunk if word is already found
                        
            except json.JSONDecodeError:
                pass # Ignore malformed chunks
            except Exception as inner_e:
                print(f"Streaming chunk error: {inner_e}")
                
    except Exception as e:
        print(f"Wake Word WebSocket disconnected: {str(e)}")

if __name__ == '__main__':
    # Run the server tightly bound to the local network so the mobile phone can access it
    app.run(host='0.0.0.0', port=5000, debug=False)

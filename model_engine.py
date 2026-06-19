"""
NxoraAI CPU-Optimized Model Engine
===================================
Tiered CPU inference: GGUF -> HuggingFace -> Gemini
"""

import os, sys, json, time, re, threading, traceback, argparse
from datetime import datetime
from typing import Optional, Callable, List, Dict, Tuple

ROOT_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
ENV_PATH    = os.path.join(ROOT_DIR, ".env")
MODELS_DIR  = os.path.join(ROOT_DIR, "models")
DATA_DIR    = os.path.join(ROOT_DIR, "data")
MEMORY_PATH = os.path.join(DATA_DIR, "chat_memory.json")

os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(DATA_DIR,   exist_ok=True)


def load_config() -> dict:
    defaults = {
        "assistant_name": "Nxora", "language": "en-US",
        "gemini_model": "gemini-1.5-flash", "max_memory_turns": 10,
        "max_new_tokens": 256, "temperature": 0.7,
        "cpu_threads": 0, "use_gemini_fallback": True,
        "confidence_threshold": 0.45,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults


def load_env() -> dict:
    data = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip()
    return data


SYSTEM_PROMPT_TEMPLATE = (
    "You are {name}, an advanced AI desktop assistant running on Windows. "
    "You are helpful, direct, and concise. "
    "Rules: Keep answers SHORT and action-oriented unless explanation is asked. "
    "For system/file commands: confirm in one sentence, then act. "
    "For code: output clean working code only, no extra commentary. "
    "If unsure, say so and offer an alternative. "
    "You run fully offline on the user's CPU -- be fast and efficient. "
    "Current date and time: {datetime}"
)


def build_system_prompt(name: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        name=name, datetime=datetime.now().strftime("%A, %d %B %Y  %H:%M")
    )


def available_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return 4.0


def cpu_thread_count() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=True) or 4
    except Exception:
        return os.cpu_count() or 4


GGUF_TIERS = [
    {"nickname": "Phi-3-mini INT4 (GGUF)", "repo": "microsoft/Phi-3-mini-4k-instruct-gguf",
     "filename": "Phi-3-mini-4k-instruct-q4.gguf", "min_ram_gb": 3.5, "ctx_size": 4096, "chat_format": "phi3"},
    {"nickname": "Qwen2.5-0.5B INT4 (GGUF)", "repo": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
     "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf", "min_ram_gb": 1.2, "ctx_size": 4096, "chat_format": "chatml"},
    {"nickname": "SmolLM2-135M INT4 (GGUF)", "repo": "bartowski/SmolLM2-135M-Instruct-GGUF",
     "filename": "SmolLM2-135M-Instruct-Q4_K_M.gguf", "min_ram_gb": 0.4, "ctx_size": 2048, "chat_format": "chatml"},
]

HF_FALLBACK_TIERS = [
    {"nickname": "SmolLM2-360M fp32 (HF)", "model_id": "HuggingFaceTB/SmolLM2-360M-Instruct", "min_ram_gb": 0.8},
    {"nickname": "SmolLM-135M fp32 (HF)", "model_id": "HuggingFaceTB/SmolLM-135M-Instruct", "min_ram_gb": 0.3},
]


def download_gguf(repo, filename, dest_dir, progress_cb=None):
    dest_path = os.path.join(dest_dir, filename)
    if os.path.exists(dest_path):
        print(f"[Engine] Cached model found: {filename}")
        return dest_path
    try:
        from huggingface_hub import hf_hub_download
        print(f"[Engine] Downloading {filename} ...")
        path = hf_hub_download(repo_id=repo, filename=filename, local_dir=dest_dir, local_dir_use_symlinks=False)
        print(f"[Engine] Download complete: {path}")
        return path
    except Exception as e:
        print(f"[Engine] HF Hub download failed ({e}), trying direct URL ...")
    try:
        import requests as req
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        r = req.get(url, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(downloaded / total)
        print(f"[Engine] Download complete: {dest_path}")
        return dest_path
    except Exception as e2:
        print(f"[Engine] Direct download also failed: {e2}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None


class ConversationMemory:
    def __init__(self, max_turns=10):
        self.max_turns = max_turns
        self._history = []
        self._load()

    def add(self, role, content):
        self._history.append({"role": role, "content": content.strip()})
        limit = self.max_turns * 2
        if len(self._history) > limit:
            self._history = self._history[-limit:]
        self._save()

    def get_messages(self, system_prompt):
        return [{"role": "system", "content": system_prompt}] + list(self._history)

    def clear(self):
        self._history = []
        self._save()

    def summary(self):
        return f"{len(self._history) // 2} turn(s) in memory (max {self.max_turns})"

    def _save(self):
        try:
            with open(MEMORY_PATH, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception:
            pass

    def _load(self):
        try:
            if os.path.exists(MEMORY_PATH):
                with open(MEMORY_PATH) as f:
                    self._history = json.load(f)
                print(f"[Memory] Loaded {len(self._history)} messages from disk.")
        except Exception:
            self._history = []


class GGUFBackend:
    def __init__(self, model_path, tier, n_threads):
        from llama_cpp import Llama
        nickname = tier["nickname"]
        print(f"[Engine] Loading {nickname} ...")
        self._llm = Llama(
            model_path=model_path, n_ctx=tier["ctx_size"],
            n_threads=n_threads, n_gpu_layers=0, verbose=False, use_mlock=True,
        )
        self._chat_format = tier["chat_format"]
        print(f"[Engine] {nickname} ready.")

    def generate(self, messages, max_tokens, temperature, stream_cb=None):
        t0 = time.time()
        output = self._llm.create_chat_completion(
            messages=messages, max_tokens=max_tokens, temperature=temperature,
            top_p=0.9, repeat_penalty=1.1, logprobs=True, stream=stream_cb is not None,
        )
        if stream_cb is not None:
            full = ""
            for chunk in output:
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    full += delta
                    stream_cb(delta)
            return full.strip(), 0.70
        choice = output["choices"][0]
        text = choice["message"]["content"].strip()
        elapsed = time.time() - t0
        confidence = self._confidence(choice)
        wps = len(text.split()) / max(elapsed, 0.1)
        print(f"[Engine] {len(text.split())} tokens in {elapsed:.1f}s ({wps:.1f} tok/s) | conf={confidence:.2f}")
        return text, confidence

    @staticmethod
    def _confidence(choice):
        import math
        try:
            lp = choice.get("logprobs") or {}
            samples = [p for p in (lp.get("token_logprobs") or [])[:20] if p is not None]
            if not samples:
                return 0.60
            return round(min(max(sum(math.exp(p) for p in samples) / len(samples), 0.0), 1.0), 3)
        except Exception:
            return 0.60


class HFBackend:
    def __init__(self, tier, n_threads):
        import torch
        import transformers
        nickname = tier["nickname"]
        print(f"[Engine] Loading {nickname} ...")
        torch.set_num_threads(n_threads)
        model_id = tier["model_id"]
        self._tok = transformers.AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        self._model = transformers.AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=torch.float32, device_map="cpu",
            trust_remote_code=True, low_cpu_mem_usage=True,
        )
        self._model.eval()
        print(f"[Engine] {nickname} ready.")

    def generate(self, messages, max_tokens, temperature, stream_cb=None):
        import torch
        if hasattr(self._tok, "apply_chat_template"):
            prompt = self._tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        else:
            prompt = self._manual_prompt(messages)
        inputs = self._tok(prompt, return_tensors="pt")
        input_ids = inputs["input_ids"]
        t0 = time.time()
        with torch.no_grad():
            out_ids = self._model.generate(
                input_ids, max_new_tokens=max_tokens,
                temperature=max(temperature, 0.01), do_sample=temperature > 0.01,
                top_p=0.9, repetition_penalty=1.1, pad_token_id=self._tok.eos_token_id,
            )
        new_ids = out_ids[0][input_ids.shape[-1]:]
        text = self._tok.decode(new_ids, skip_special_tokens=True).strip()
        elapsed = time.time() - t0
        print(f"[Engine] {len(new_ids)} tokens in {elapsed:.1f}s ({len(new_ids)/max(elapsed,0.1):.1f} tok/s)")
        if stream_cb:
            stream_cb(text)
        return text, 0.65

    @staticmethod
    def _manual_prompt(messages):
        parts = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            parts.append(f"<|{role}|>\n{content}")
        parts.append("<|assistant|>")
        return "\n".join(parts)


class GeminiBackend:
    def __init__(self, api_key, model_name):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        print(f"[Engine] Gemini direct backend ready ({model_name}).")

    def generate(self, messages, max_tokens, temperature):
        from google.genai import types
        contents = []
        system_instruction = None
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=content)]))
            elif role == "assistant":
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=content)]))

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        resp = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )
        return resp.text.strip()


def clean_response(text):
    text = re.sub(r"^(<\|.*?\|>|<<SYS>>.*?<</SYS>>|</?s>)\s*", "", text, flags=re.DOTALL)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if sentences and not re.search(r"[.!?\"']$", sentences[-1].strip()) and len(sentences) > 1:
        text = " ".join(sentences[:-1])
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class NxoraAIEngine:
    """
    Single entry point for all AI inference in NxoraAI.
    Usage:
        engine = NxoraAIEngine()
        response = engine.chat("What time is it?")
    """

    def __init__(self, progress_cb=None):
        self.cfg = load_config()
        self.env = load_env()
        self.memory = ConversationMemory(max_turns=self.cfg.get("max_memory_turns", 10))
        self._local = None
        self._gemini = None
        self._lock = threading.Lock()
        self._backend = "none"
        self._progress_cb = progress_cb
        
        # Initialize Gemini first to be lightweight and fast
        self._init_gemini()
        if self._gemini:
            self._backend = f"gemini:{self.cfg.get('gemini_model', 'gemini-1.5-flash')}"
        else:
            self._init_local(progress_cb)

    def _init_local(self, progress_cb=None):
        ram = available_ram_gb()
        threads = self.cfg.get("cpu_threads", 0) or cpu_thread_count()
        print(f"[Engine] RAM available: {ram:.1f} GB  |  CPU threads: {threads}")

        if self._llama_cpp_available():
            for tier in GGUF_TIERS:
                if ram < tier["min_ram_gb"]:
                    print(f"[Engine] Skipping {tier['nickname']} (need {tier['min_ram_gb']} GB)")
                    continue
                path = download_gguf(tier["repo"], tier["filename"], MODELS_DIR, progress_cb)
                if path:
                    try:
                        self._local = GGUFBackend(path, tier, threads)
                        self._backend = f"gguf:{tier['nickname']}"
                        return
                    except Exception as e:
                        print(f"[Engine] {tier['nickname']} load error: {e}")
        else:
            print("[Engine] llama-cpp-python not found -- using HuggingFace (slower).")
            print("[Engine] To get 3-5x faster CPU inference, install it:")
            print("[Engine]   pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu")

        for tier in HF_FALLBACK_TIERS:
            if ram < tier["min_ram_gb"]:
                continue
            try:
                self._local = HFBackend(tier, threads)
                self._backend = f"hf:{tier['nickname']}"
                return
            except Exception as e:
                print(f"[Engine] HF tier {tier['nickname']} failed: {e}")

        print("[Engine] WARNING -- no local backend loaded!")

    def _init_gemini(self):
        if not self.cfg.get("use_gemini_fallback", True):
            return
        key = self.env.get("GEMINI_API_KEY", "")
        if not key:
            key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return
        try:
            self._gemini = GeminiBackend(key, self.cfg.get("gemini_model", "gemini-1.5-flash"))
        except Exception as e:
            print(f"[Engine] Gemini init failed: {e}")

    @staticmethod
    def _llama_cpp_available():
        try:
            import llama_cpp
            return True
        except ImportError:
            return False

    def chat(self, user_message, stream_cb=None, use_memory=True):
        with self._lock:
            return self._infer(user_message, stream_cb, use_memory)

    def chat_async(self, user_message, on_done=None, on_error=None, stream_cb=None, use_memory=True):
        def _worker():
            try:
                resp = self.chat(user_message, stream_cb, use_memory)
                if on_done:
                    on_done(resp)
            except Exception as exc:
                traceback.print_exc()
                if on_error:
                    on_error(exc)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    def clear_memory(self):
        self.memory.clear()

    def memory_info(self):
        return self.memory.summary()

    def backend_info(self):
        return self._backend

    def _infer(self, user_message, stream_cb, use_memory):
        name = self.cfg.get("assistant_name", "Nxora")
        max_tk = int(self.cfg.get("max_new_tokens", 256))
        temp = float(self.cfg.get("temperature", 0.7))
        thresh = float(self.cfg.get("confidence_threshold", 0.45))
        syspmt = build_system_prompt(name)

        if use_memory:
            self.memory.add("user", user_message)
            messages = self.memory.get_messages(syspmt)
        else:
            messages = [
                {"role": "system", "content": syspmt},
                {"role": "user", "content": user_message},
            ]

        response = None
        confidence = 0.0
        used_gemini = False

        # Try Gemini first if available (high-performance & lightweight routing)
        if self._gemini:
            try:
                print("[Engine] Routing query directly to Gemini...")
                g = self._gemini.generate(messages, max_tk, temp)
                if g:
                    response = g
                    used_gemini = True
            except Exception as e:
                print(f"[Engine] Gemini error: {e}")
                # If Gemini fails, lazily load local backend if not loaded
                if not self._local:
                    self._init_local(self._progress_cb)

        # Fallback to local if Gemini was not available or failed
        if not response:
            if not self._local:
                self._init_local(self._progress_cb)
            if self._local:
                try:
                    raw, confidence = self._local.generate(messages, max_tk, temp, stream_cb)
                    response = clean_response(raw)
                except Exception as e:
                    print(f"[Engine] Local inference error: {e}")
                    traceback.print_exc()

        if not response:
            response = (
                "I couldn't generate a response right now. "
                "Please check your internet connection or ensure a local model file is in the /models folder."
            )

        if use_memory:
            self.memory.add("assistant", response)

        src = "Gemini" if used_gemini else self._backend
        print(f"[Engine] Source: {src}")
        return response


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NxoraAI Model Engine")
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--chat", type=str)
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()

    print("[NxoraAI] Starting engine ...")
    engine = NxoraAIEngine()
    print(f"[NxoraAI] Backend : {engine.backend_info()}")
    print(f"[NxoraAI] Memory  : {engine.memory_info()}")

    if args.clear:
        engine.clear_memory()
        sys.exit(0)

    if args.chat:
        print(f"\nYou: {args.chat}")
        r = engine.chat(args.chat)
        name = engine.cfg.get("assistant_name", "Nxora")
        print(f"\n{name}: {r}\n")
        sys.exit(0)

    name = engine.cfg.get("assistant_name", "Nxora")
    print(f"\n[NxoraAI] Interactive mode  |  'quit' to exit  |  'clear' to reset memory\n")
    while True:
        try:
            user_in = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break
        if not user_in:
            continue
        if user_in.lower() in ("quit", "exit", "q"):
            break
        if user_in.lower() == "clear":
            engine.clear_memory()
            continue
        resp = engine.chat(user_in)
        print(f"\n{name}: {resp}\n")

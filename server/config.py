"""Runtime configuration. Everything comes from environment variables (.env supported)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


GNANI_API_KEY = _env("GNANI_API_KEY", "")

# --- STT: Prisma v3.0 realtime ---
STT_URL = _env("GNANI_STT_URL", "wss://api.vachana.ai/stt/v3/stream")
STT_MODEL = "prisma-v3.0"  # required by the challenge
STT_FORMAT = _env("STT_FORMAT", "transcribe")  # digits for numbers; LLM handles code-mix scripts
STT_SAMPLE_RATE = 16000
STT_FRAME_BYTES = 1024  # 512 samples x 16-bit
# VAD tuned for household / street noise: stricter speech gate, dictation-length silence.
VAD = {
    # Measured on the live API: 0.5 / 120 / 120 keeps a one-word "हाँ" at 5 dB SNR (0.6 / 200 / 200 dropped it)
    # while pure street noise still produces no transcript.
    "x-vad-threshold": _env("VAD_THRESHOLD", "0.5"),
    "x-min-speech-ms": _env("VAD_MIN_SPEECH_MS", "120"),
    "x-min-segment-ms": _env("VAD_MIN_SEGMENT_MS", "120"),
    "x-min-silence-ms": _env("VAD_MIN_SILENCE_MS", "600"),
    "x-max-speech-s": _env("VAD_MAX_SPEECH_S", "25"),
}

# --- TTS: Timbre v3.0 realtime ---
TTS_URL = _env("GNANI_TTS_URL", "wss://api.vachana.ai/api/v1/tts")
TTS_MODEL = "timbre-v3.0"  # required by the challenge
TTS_VOICE = _env("TTS_VOICE", "Shivani")
TTS_SPEED = float(_env("TTS_SPEED", "1.0"))
TTS_SAMPLE_RATE = 24000

# --- LLM ---
# Default: Groq (free tier, no card, very fast). Any OpenAI-compatible endpoint works the same way.
# LLM_PROVIDER=anthropic uses Claude instead.
_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
               "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "llama3.1:8b", "OLLAMA_API_KEY"),
    "anthropic": ("", "claude-opus-5-5", "ANTHROPIC_API_KEY"),
}
LLM_PROVIDER = _env("LLM_PROVIDER", "groq").lower()
_base, _model, _keyvar = _PRESETS.get(LLM_PROVIDER, _PRESETS["groq"])
LLM_BASE_URL = _env("LLM_BASE_URL", _base)
LLM_MODEL = _env("LLM_MODEL", _model)
LLM_API_KEY = _env("LLM_API_KEY", "") or _env(_keyvar, "")
# Fallback chain: each Groq model has its own free per-minute token budget, so a 429 moves to the next one.
_DEFAULT_CHAIN = {"groq": "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"}
LLM_MODELS = [m.strip() for m in _env("LLM_MODELS", _DEFAULT_CHAIN.get(LLM_PROVIDER, LLM_MODEL)
                                      if "LLM_MODEL" not in os.environ else LLM_MODEL).split(",") if m.strip()]
LLM_EFFORT = _env("LLM_EFFORT", "low")  # anthropic only
LLM_FAST = _env("LLM_FAST", "0") == "1"  # anthropic only: Opus 5.5 fast mode (premium pricing)

DATA_DIR = ROOT / "data"
RECORDS_FILE = DATA_DIR / "records.jsonl"
REGISTRY_FILE = DATA_DIR / "beneficiaries.json"

# Languages we expose in the UI: (code, native name, english name)
LANGUAGES = [
    ("hi-IN", "हिन्दी", "Hindi"),
    ("en-IN", "English", "English"),
    ("mr-IN", "मराठी", "Marathi"),
    ("bn-IN", "বাংলা", "Bengali"),
    ("gu-IN", "ગુજરાતી", "Gujarati"),
    ("pa-IN", "ਪੰਜਾਬੀ", "Punjabi"),
    ("ta-IN", "தமிழ்", "Tamil"),
    ("te-IN", "తెలుగు", "Telugu"),
    ("kn-IN", "ಕನ್ನಡ", "Kannada"),
    ("ml-IN", "മലയാളം", "Malayalam"),
]
LANG_CODES = {c for c, _, _ in LANGUAGES}
LANG_NAMES = {c: e for c, _, e in LANGUAGES}

# Local fast path (server/fastpath.py): routine dictation in Hindi/English is parsed without an LLM call.
FASTPATH = _env("FASTPATH", "1") not in ("0", "false", "no")

# Automatic backups: any OTHER free provider whose key is in .env joins the end of the fallback chain
# (e.g. add GEMINI_API_KEY so that when every Groq model is out of daily budget the next turn uses Gemini).
LLM_BACKUPS = []  # (label, base_url, api_key, model)
for _p in ("groq", "gemini", "openrouter"):
    if _p == LLM_PROVIDER:
        continue
    _b, _m, _kv = _PRESETS[_p]
    _k = _env(_kv, "")
    if _k:
        LLM_BACKUPS.append((f"{_p}:{_m}", _b, _k, _m))

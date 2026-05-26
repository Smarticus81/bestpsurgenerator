"""Configuration."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

CONSTRAINTS_DIR = BASE_DIR / "constraints"

# Canonical constraint file paths (renamed for clarity)
TEMPLATE_SCHEMA_PATH = CONSTRAINTS_DIR / "template_schema.json"
SECTION_GUIDANCE_PATH = CONSTRAINTS_DIR / "section_guidance.json"
MDCG_KB_PATH = CONSTRAINTS_DIR / "mdcg_2022_21_knowledge_base.json"
DOCX_TEMPLATE_PATH = CONSTRAINTS_DIR / "FormQAR-054_template.docx"


INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-opus-4-7"

# Approved reasoning-model policy. These are the only models allowed for deep
# narrative, audit, remediation, and orchestration work.
APPROVED_REASONING_MODELS = {
    "claude-opus-4-7": {
        "provider": "anthropic",
        "aliases": {"claude-opus-4.7", "claude opus 4.7", "opus-4.7", "opus 4.7"},
    },
    "claude-opus-4-6": {
        "provider": "anthropic",
        "aliases": {"claude-opus-4.6", "claude opus 4.6", "opus-4.6", "opus 4.6"},
    },
    "gpt-5.5": {
        "provider": "openai",
        "aliases": {"gpt-5.5", "gpt 5.5"},
    },
    "gemini-3.5-flash": {
        "provider": "google",
        "aliases": {"gemini-3.5", "gemini 3.5", "gemini-3.5-flash", "gemini 3.5 flash"},
    },
    "deepseek-r1": {
        "provider": "ollama",
        "aliases": {"deepseek-r1", "deepseek r1", "deepseek-r1:latest"},
    },
    "qwq": {
        "provider": "ollama",
        "aliases": {"qwq-32b", "qwen/qwq-32b", "qwen-qwq-32b", "qwq:32b", "qwq32b"},
    },
}


def normalize_reasoning_model(model: str) -> str:
    raw = (model or "").strip()
    folded = raw.lower()
    for canonical, meta in APPROVED_REASONING_MODELS.items():
        aliases = set(meta["aliases"]) | {canonical}
        if folded in {alias.lower() for alias in aliases}:
            return canonical
    allowed = ", ".join(APPROVED_REASONING_MODELS)
    raise ValueError(f"Unsupported reasoning model '{model}'. Approved reasoning models: {allowed}")

# OpenAI fallback (used when Anthropic quota/rate-limit is hit)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_FALLBACK_MODEL = normalize_reasoning_model(os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-5.5"))

# Reasoning model — used for narratives, tables, and validation/quality
MODEL_REASONING = normalize_reasoning_model(os.environ.get("REASONING_MODEL", "claude-opus-4-7"))

# Ollama local model support (native /api/chat endpoint)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")          # legacy local override; prefer OLLAMA_REASONING_MODEL
OLLAMA_REASONING_MODEL = (
    normalize_reasoning_model(os.environ["OLLAMA_REASONING_MODEL"])
    if os.environ.get("OLLAMA_REASONING_MODEL") else ""
)
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "32768"))  # context window size

# Additional constraint file paths
RACT_CODES_PATH = CONSTRAINTS_DIR / "ract_occurrence_codes.json"
HARM_MDP_PATH = CONSTRAINTS_DIR / "harm_mdp_codes.csv"

DATABASE_URL = os.environ.get("DATABASE_URL")

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
DOCX_TEMPLATE_PATH = CONSTRAINTS_DIR / "rg_psur_001_template.docx"


INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
# claude-sonnet-4-20250514 was retired 2026-06-15; override via ANTHROPIC_MODEL env.
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
MODEL = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)

# OpenAI fallback (used when Anthropic quota/rate-limit is hit)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_FALLBACK_MODEL = os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-4.1")

# Reasoning model — used for narratives, tables, and validation/quality
MODEL_REASONING = os.environ.get("REASONING_MODEL", _DEFAULT_ANTHROPIC_MODEL)

# Ollama local model support (native /api/chat endpoint)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")          # e.g. "qwen3:32b"
OLLAMA_REASONING_MODEL = os.environ.get("OLLAMA_REASONING_MODEL", "")  # e.g. "deepseek-r1:70b"
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "32768"))  # context window size

# Additional constraint file paths
RACT_CODES_PATH = CONSTRAINTS_DIR / "ract_occurrence_codes.json"
HARM_MDP_PATH = CONSTRAINTS_DIR / "harm_mdp_codes.csv"

DATABASE_URL = os.environ.get("DATABASE_URL")

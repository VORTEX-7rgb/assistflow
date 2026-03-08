"""
RapidRAG — config.py  (FIXED)

BUGS FIXED vs previous version:
  1. WebsiteIngestRequest had a model_validate override that DOESN'T FIRE
     during FastAPI request body parsing. Changed to @field_validator.

  2. scraper_timeout was 10s — bumped to 25s (slow/shared-hosting sites).

  3. llm_timeout was 10s — bumped to 20s (Groq can be slow on free tier).
"""

import os
from pydantic_settings import BaseSettings
from pydantic import BaseModel, Field, field_validator
from typing import Optional

# ── Suppress ChromaDB telemetry bug (chromadb 0.5.x) ──────────────
# chromadb's telemetry calls posthog.capture() with wrong args,
# causing "capture() takes 1 positional argument but 3 were given".
# This is harmless but spams the console. Fix: disable telemetry.
os.environ["ANONYMIZED_TELEMETRY"] = "False"
try:
    from unittest.mock import MagicMock
    import sys
    sys.modules["chromadb.telemetry.product.posthog"] = MagicMock()
except Exception:
    pass


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────
# Global Application Settings
# ─────────────────────────────────────────────

class Settings(BaseSettings):
    """Global configuration loaded from environment variables + .env file."""

    # ── App ──
    app_name:    str  = "RapidRAG"
    app_version: str  = "1.0.0"
    debug:       bool = False
    project_root: str = PROJECT_ROOT
    base_client_path: str = os.path.join(PROJECT_ROOT, "clients")

    # ── Embedding ──
    embedding_model:     str = "all-MiniLM-L6-v2"
    embedding_dimension: int = 384
    embedding_batch_size: int = 64

    # ── Chunking ──
    chunk_size:       int       = 512
    chunk_overlap:    int       = 64
    chunk_separators: list[str] = ["\n\n", "\n", ". ", " "]

    # ── Retrieval ──
    default_top_k:              int   = 5
    similarity_floor:           float = 0.25
    dynamic_threshold_factor:   float = 0.7
    max_context_tokens:         int   = 3000

    # ── LLM ──
    default_llm_model:         str = "groq/llama-3.3-70b-versatile"
    fallback_llm_models:       list[str] = [
        "groq/llama-3.3-70b-versatile",
        "openrouter/meta-llama/llama-3.3-70b-instruct:free",
        "groq/qwen-2.5-32b",
        "groq/llama-3.1-8b-instant"
    ]
    llm_timeout:               int = 20    # FIX: was 10 — Groq free tier can be slow
    llm_max_tokens:            int = 500
    llm_max_connections:       int = 20
    llm_keepalive_connections: int = 10

    # ── Provider API Keys ──
    groq_api_key:       Optional[str] = None
    openai_api_key:     Optional[str] = None
    together_api_key:   Optional[str] = None
    openrouter_api_key: Optional[str] = None

    # ── Scraper ──
    scraper_max_pages:   int = 300
    scraper_concurrency: int = 8
    scraper_timeout:     int = 25   # FIX: was 10 — too short for slow sites

    # ── Client Management ──
    max_clients_loaded: int = 10
    preload_count:      int = 5

    # ── Cache ──
    cache_ttl_seconds: int = 3600
    cache_dir:         str = os.path.join(PROJECT_ROOT, "cache")

    # ── Rate Limiting ──
    rate_limit_per_minute: int = 60

    # ── Logging ──
    log_dir:   str = os.path.join(PROJECT_ROOT, "logs")
    log_level: str = "INFO"

    # ── CORS ──
    cors_origins: str = ""

    # ── Admin Auth ──
    admin_api_key: str = ""   # Set ADMIN_API_KEY in .env for production

    class Config:
        env_file = os.path.join(PROJECT_ROOT, ".env")
        env_file_encoding = "utf-8"


# ─────────────────────────────────────────────
# LLM Provider Registry
# ─────────────────────────────────────────────

LLM_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
    },
    "local": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
    },
}


# ─────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────

class RAGResponse(BaseModel):
    reply:      str
    intent:     str        = "unknown"
    lead:       bool       = False
    confidence: float      = 0.0
    sources:    list[str]  = Field(default_factory=list)
    cache_hit:  bool       = False
    user_name:  str        = ""
    user_phone: str        = ""
    user_email: str        = ""
    requirement: str       = ""
    budget:     str        = ""
    collected:  bool       = False


class Chunk(BaseModel):
    text:        str
    source:      str
    chunk_index: int   = 0
    score:       float = 0.0


class ScrapedPage(BaseModel):
    url:        str
    title:      str = ""
    clean_text: str = ""
    word_count: int = 0


class ClientConfig(BaseModel):
    client_id:           str
    is_active:           bool  = True
    business_name:       str
    business_type:       str   = "business"
    website_url:         str   = ""
    contact_email:       str   = ""
    contact_phone:       str   = ""
    n8n_webhook_url:     str   = ""
    owner_email:         str   = ""
    bot_name:            str   = "Assistant"
    logo_url:            str   = ""
    instagram_page_id:   str   = ""
    manychat_api_key:    str   = ""
    instagram_access_token: str = ""
    instagram_channel:   str   = ""              # "manychat" or "meta_api" or ""
    embedding_model:     str   = "all-MiniLM-L6-v2"
    llm_model:           str   = "groq/llama-3.1-8b-instant"
    similarity_threshold: float = 0.35
    top_k:               int   = 5
    max_response_tokens: int   = 500
    prompt_version:      int   = 1
    cache_ttl:           int   = 3600


class IngestionResult(BaseModel):
    status:          str       = "completed"
    pages_scraped:   int       = 0
    pages_parsed:    int       = 0
    chunks_created:  int       = 0
    vectors_stored:  int       = 0
    embedding_model: str       = ""
    errors:          list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str = ""


class WebsiteIngestRequest(BaseModel):
    """
    FIX: Old code used model_validate override for URL normalization,
    which does NOT fire during FastAPI's automatic request parsing.
    Changed to @field_validator which DOES fire.
    """
    url: str = Field(..., min_length=5)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        return v


class ClientCreateRequest(BaseModel):
    client_id:         str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9_]+$")
    business_name:     str = Field(..., min_length=2, max_length=200)
    business_type:     str = "business"
    website_url:       str = ""
    contact_email:     str = ""
    contact_phone:     str = ""
    n8n_webhook_url:   str = ""
    owner_email:       str = ""
    bot_name:          str = "Assistant"
    logo_url:          str = ""
    instagram_page_id: str = ""
    manychat_api_key:  str = ""
    instagram_access_token: str = ""
    instagram_channel:  str = ""


# ─────────────────────────────────────────────
# Intent Categories
# ─────────────────────────────────────────────

INTENT_CATEGORIES = [
    "pricing_query", "service_inquiry", "appointment_request",
    "contact_request", "faq", "complaint", "general", "unknown",
]

LEAD_INTENTS = {"appointment_request", "contact_request", "complaint", "pricing_query", "service_inquiry"}


# ─────────────────────────────────────────────
# Singleton Settings Instance
# ─────────────────────────────────────────────

settings = Settings()
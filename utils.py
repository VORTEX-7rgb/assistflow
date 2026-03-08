"""
RapidRAG — utils.py
Utility functions: query normalization, context deduplication,
JSON parsing with fallback, and text helpers.
"""

import re
import json
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Query Normalization (Fix 4)
# ─────────────────────────────────────────────

# Regex to strip emojis and special unicode
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def normalize_query(query: str) -> str:
    """
    Normalize user query for consistent embedding & cache matching.
    - Lowercase
    - Strip whitespace
    - Remove emojis
    - Collapse multiple spaces
    - Remove trailing punctuation clusters (??!!)
    """
    q = query.lower().strip()
    q = _EMOJI_PATTERN.sub("", q)
    q = re.sub(r"\s+", " ", q)              # collapse whitespace
    q = re.sub(r"[?!.]{2,}$", "?", q)       # "price??" → "price?"
    return q.strip()


# ─────────────────────────────────────────────
# Context Deduplication (Fix 7)
# ─────────────────────────────────────────────

def deduplicate_sentences(text: str) -> str:
    """
    Remove duplicate sentences across concatenated chunks.
    Saves 20-40% tokens → faster LLM responses, cheaper.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    seen = set()
    unique = []

    for sentence in sentences:
        normalized = sentence.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(sentence)

    return " ".join(unique)


# ─────────────────────────────────────────────
# JSON Parsing with Fallback (Fix 3)
# ─────────────────────────────────────────────

def safe_json_parse(raw: str) -> dict:
    """
    Parse LLM JSON response with multi-level fallback.
    Handles: raw JSON, markdown-wrapped JSON, partial JSON.
    """
    if not raw or not raw.strip():
        return {"reply": "", "intent": "unknown", "lead": False}

    cleaned = raw.strip()

    # Level 1: Direct JSON parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Level 2: Extract from markdown code block ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Level 3: Find first { ... } block (greedy -> non-greedy)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Level 4: Complete failure — wrap raw text as reply
    logger.warning(f"Failed to parse LLM JSON, using raw text fallback")
    return {"reply": cleaned, "intent": "unknown", "lead": False}


# ─────────────────────────────────────────────
# Context Truncation
# ─────────────────────────────────────────────

def truncate_context(text: str, max_tokens: int = 3000) -> str:
    """
    Truncate context to fit within token budget.
    Rough estimate: 1 token ≈ 4 characters.
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    # Truncate at last sentence boundary before limit
    truncated = text[:max_chars]
    last_period = truncated.rfind(". ")
    if last_period > max_chars * 0.5:
        return truncated[: last_period + 1]
    return truncated

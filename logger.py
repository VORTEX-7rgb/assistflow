"""
RapidRAG — logger.py
Structured JSON logging with per-client context,
request tracing, and analytics-ready event emission.
"""

import os
import json
import logging
from datetime import datetime, timezone
from config import settings


# ─────────────────────────────────────────────
# JSON Formatter for Production Logs
# ─────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Format log records as JSON lines for easy parsing by log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Add extra fields if present
        if hasattr(record, "client_id"):
            log_entry["client_id"] = record.client_id
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id

        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = str(record.exc_info[1])
            log_entry["error_type"] = record.exc_info[0].__name__

        return json.dumps(log_entry, ensure_ascii=False)


# ─────────────────────────────────────────────
# Structured Logger
# ─────────────────────────────────────────────

class StructuredLogger:
    """
    Production logging with:
    - Structured JSON output per request
    - Per-client log files (logs/{client_id}/{date}.jsonl)
    - Analytics event hooks (future: webhook, n8n)
    - Latency, intent, and lead tracking
    """

    def __init__(self, log_dir: str | None = None):
        self.log_dir = log_dir or settings.log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    # ─────────────────────────────────────────
    # Chat Request Logging
    # ─────────────────────────────────────────

    def log_chat_request(
        self,
        client_id: str,
        query: str,
        reply: str,
        intent: str,
        lead: bool,
        confidence: float,
        sources: list[str],
        latency_ms: int,
        cache_hit: bool,
        prompt_version: int = 1,
        embedding_model: str = "",
    ) -> dict:
        """
        Log a complete chat request/response cycle.
        Returns the log entry dict (also used as analytics event).
        """
        entry = {
            "event": "chat_response",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_id": client_id,
            "query": query,
            "reply_preview": reply[:200],  # truncate for log readability
            "intent": intent,
            "lead": lead,
            "confidence": confidence,
            "sources": sources,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "prompt_version": prompt_version,
            "embedding_model": embedding_model,
        }

        # Write to main log
        logging.getLogger("rapidrag.chat").info(json.dumps(entry))

        # Write to per-client log file
        self._write_client_log(client_id, entry)

        return entry

    # ─────────────────────────────────────────
    # Ingestion Logging
    # ─────────────────────────────────────────

    def log_ingestion(
        self,
        client_id: str,
        source_type: str,  # "website" or "pdf"
        pages_scraped: int,
        chunks_created: int,
        vectors_stored: int,
        latency_ms: int,
        errors: list[str] | None = None,
    ) -> dict:
        """Log an ingestion operation."""
        entry = {
            "event": "ingestion",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_id": client_id,
            "source_type": source_type,
            "pages_scraped": pages_scraped,
            "chunks_created": chunks_created,
            "vectors_stored": vectors_stored,
            "latency_ms": latency_ms,
            "errors": errors or [],
        }

        logging.getLogger("rapidrag.ingestion").info(json.dumps(entry))
        self._write_client_log(client_id, entry)

        return entry

    # ─────────────────────────────────────────
    # Error Logging
    # ─────────────────────────────────────────

    def log_error(
        self,
        client_id: str,
        error_type: str,
        error_message: str,
        context: dict | None = None,
    ) -> None:
        """Log an error with context."""
        entry = {
            "event": "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_id": client_id,
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {},
        }

        logging.getLogger("rapidrag.errors").error(json.dumps(entry))
        self._write_client_log(client_id, entry)

    # ─────────────────────────────────────────
    # Slow Query Warning
    # ─────────────────────────────────────────

    def log_slow_query(
        self, client_id: str, query: str, latency_ms: int
    ) -> None:
        """Log a warning for queries exceeding 3 seconds."""
        if latency_ms > 3000:
            entry = {
                "event": "slow_query",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "client_id": client_id,
                "query": query,
                "latency_ms": latency_ms,
            }
            logging.getLogger("rapidrag.performance").warning(
                json.dumps(entry)
            )

    # ─────────────────────────────────────────
    # Per-Client Log Files
    # ─────────────────────────────────────────

    def _write_client_log(self, client_id: str, entry: dict) -> None:
        """Append entry to per-client daily log file (JSONL format)."""
        try:
            client_log_dir = os.path.join(self.log_dir, client_id)
            os.makedirs(client_log_dir, exist_ok=True)

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_path = os.path.join(client_log_dir, f"{date_str}.jsonl")

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        except OSError as e:
            logging.getLogger("rapidrag").debug(
                f"Failed to write client log: {e}"
            )

    # ─────────────────────────────────────────
    # Log Retrieval (for admin/debugging)
    # ─────────────────────────────────────────

    def get_recent_logs(
        self, client_id: str, limit: int = 50
    ) -> list[dict]:
        """Read the most recent log entries for a client (newest first)."""
        client_log_dir = os.path.join(self.log_dir, client_id)
        if not os.path.exists(client_log_dir):
            return []

        # Find most recent log file (sorted newest first)
        log_files = sorted(
            [f for f in os.listdir(client_log_dir) if f.endswith(".jsonl")],
            reverse=True,
        )

        entries = []
        for log_file in log_files:
            if len(entries) >= limit:
                break
            log_path = os.path.join(client_log_dir, log_file)
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    file_entries = []
                    for line in f:
                        if line.strip():
                            file_entries.append(json.loads(line))
                    entries.extend(file_entries)
            except (OSError, json.JSONDecodeError):
                continue

        entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return entries[:limit]  # already newest-first


# ─────────────────────────────────────────────
# Configure Logging for Production
# ─────────────────────────────────────────────

def setup_logging(debug: bool = False) -> None:
    """
    Configure logging for the entire application.
    - Development: human-readable format
    - Production: JSON formatter for log aggregation
    """
    root_logger = logging.getLogger("rapidrag")
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console = logging.StreamHandler()

    if debug:
        # Human-readable for development
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    else:
        # JSON for production
        console.setFormatter(JSONFormatter())

    console.setLevel(logging.DEBUG if debug else logging.INFO)
    root_logger.addHandler(console)

    # File handler (always JSON, always INFO+)
    log_dir = settings.log_dir
    os.makedirs(log_dir, exist_ok=True)

    file_handler = logging.FileHandler(
        os.path.join(log_dir, "rapidrag.log"),
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)


# ─────────────────────────────────────────────
# Singleton Instance
# ─────────────────────────────────────────────

structured_logger = StructuredLogger()

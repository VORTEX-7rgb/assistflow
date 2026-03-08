"""
RapidRAG — errors.py
Custom exception hierarchy for structured error handling.
Every error type maps to a specific HTTP status code and behavior.
"""


class RapidRAGError(Exception):
    """Base exception for all RapidRAG errors."""
    status_code: int = 500
    detail: str = "An internal error occurred"

    def __init__(self, detail: str | None = None):
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


# ─────────────────────────────────────────────
# Client Errors (4xx)
# ─────────────────────────────────────────────

class ClientNotFoundError(RapidRAGError):
    """Client ID does not exist."""
    status_code = 404
    detail = "Client not found"


class ClientAlreadyExistsError(RapidRAGError):
    """Attempted to create a client that already exists."""
    status_code = 409
    detail = "Client already exists"


class IngestionLockError(RapidRAGError):
    """Ingestion is already in progress for this client."""
    status_code = 409
    detail = "Ingestion already in progress for this client"


class ValidationError(RapidRAGError):
    """Invalid input data."""
    status_code = 422
    detail = "Invalid input data"


class RateLimitError(RapidRAGError):
    """Too many requests."""
    status_code = 429
    detail = "Rate limit exceeded, please try again later"


# ─────────────────────────────────────────────
# Retrieval & Generation Errors (5xx)
# ─────────────────────────────────────────────

class InsufficientContextError(RapidRAGError):
    """No relevant context found for the query."""
    status_code = 200  # Not a server error — valid "I don't know" response
    detail = "Insufficient context to answer this query"


class EmbeddingMismatchError(RapidRAGError):
    """Stored vectors were built with a different embedding model."""
    status_code = 503
    detail = "Embedding model mismatch — re-ingestion required"


class LLMTimeoutError(RapidRAGError):
    """LLM API call timed out after retries."""
    status_code = 504
    detail = "AI response timed out, please try again"


class LLMRateLimitError(RapidRAGError):
    """LLM provider rate limit hit."""
    status_code = 429
    detail = "AI service rate limit reached, please wait"


class LLMUnavailableError(RapidRAGError):
    """LLM provider is down or unreachable."""
    status_code = 503
    detail = "AI service temporarily unavailable"


# ─────────────────────────────────────────────
# Ingestion Errors
# ─────────────────────────────────────────────

class IngestionError(RapidRAGError):
    """Generic ingestion pipeline failure."""
    status_code = 500
    detail = "Ingestion pipeline error"


class ScrapingError(RapidRAGError):
    """Web scraping failed."""
    status_code = 502
    detail = "Failed to scrape the target website"


class PDFExtractionError(RapidRAGError):
    """PDF text extraction failed."""
    status_code = 422
    detail = "Failed to extract text from PDF"


# ─────────────────────────────────────────────
# Error → HTTP Response Mapping
# ─────────────────────────────────────────────

ERROR_RESPONSES = {
    ClientNotFoundError: {"status_code": 404, "detail": "Client not found"},
    ClientAlreadyExistsError: {"status_code": 409, "detail": "Client already exists"},
    IngestionLockError: {"status_code": 409, "detail": "Ingestion in progress"},
    RateLimitError: {"status_code": 429, "detail": "Rate limit exceeded"},
    LLMTimeoutError: {"status_code": 504, "detail": "AI response timed out"},
    LLMUnavailableError: {"status_code": 503, "detail": "AI service unavailable"},
    EmbeddingMismatchError: {"status_code": 503, "detail": "Model mismatch"},
}

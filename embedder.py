"""
RapidRAG — embedder.py
Embedding pipeline for single queries and batch processing.
Uses sentence-transformers locally — zero cost, fast inference.
"""

import logging
from sentence_transformers import SentenceTransformer
from config import settings

logger = logging.getLogger(__name__)


class Embedder:
    """
    Handles all text → vector embedding operations.
    - Single query embedding for retrieval (<10ms)
    - Batch embedding for ingestion (64 texts/batch)
    - Model pre-loaded at startup for zero cold-start latency
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.embedding_model
        self.model: SentenceTransformer | None = None
        self.dimension = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size

    # ─────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────

    def load_model(self) -> None:
        """Pre-load embedding model into memory. Call once at startup."""
        if self.model is not None:
            return
        logger.info(f"Loading embedding model: {self.model_name}")
        self.model = SentenceTransformer(self.model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()
        logger.info(
            f"Embedding model loaded: {self.model_name} "
            f"(dim={self.dimension})"
        )

    def _ensure_loaded(self) -> None:
        """Lazy-load model if not yet initialized."""
        if self.model is None:
            self.load_model()

    # ─────────────────────────────────────────
    # Single Query Embedding
    # ─────────────────────────────────────────

    async def embed_query(self, text: str) -> list[float]:
        """
        Embed a single query string. Used at retrieval time.
        Target latency: <10ms after model is loaded.
        """
        self._ensure_loaded()
        import asyncio
        embedding = await asyncio.to_thread(
            self.model.encode,
            text,
            normalize_embeddings=True,    # L2 normalized for cosine sim
            show_progress_bar=False,
        )
        return embedding.tolist()

    # ─────────────────────────────────────────
    # Batch Embedding (Ingestion)
    # ─────────────────────────────────────────

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of texts. Used during ingestion.
        Processes in chunks of `batch_size` for memory efficiency.
        200 chunks → 4 batches of 64 → ~10x faster than individual calls.
        """
        self._ensure_loaded()

        if not texts:
            return []

        all_embeddings = []

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            import asyncio
            batch_embeddings = await asyncio.to_thread(
                self.model.encode,
                batch,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=self._batch_size,
            )
            all_embeddings.extend(batch_embeddings.tolist())

            logger.debug(
                f"Embedded batch {i // self._batch_size + 1}: "
                f"{len(batch)} texts"
            )

        logger.info(f"Batch embedding complete: {len(texts)} texts total")
        return all_embeddings

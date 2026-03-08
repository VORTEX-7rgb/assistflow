"""
RapidRAG — chunker.py
Recursive text chunking with overlap.
Splits documents by semantic boundaries (paragraphs → sentences → words).
"""

import re
import logging
from config import settings, Chunk

logger = logging.getLogger(__name__)


class Chunker:
    """
    Recursive character text splitter.
    Strategy: split by largest separator first, then recursively split
    oversized chunks by the next separator in the hierarchy.

    Hierarchy: paragraphs → newlines → sentences → words
    Default:   512 tokens (~2000 chars), 12.5% overlap (~250 chars)
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        separators: list[str] | None = None,
    ):
        # Convert from tokens to chars (1 token ≈ 4 chars)
        token_size = chunk_size or settings.chunk_size
        token_overlap = chunk_overlap or settings.chunk_overlap

        self.chunk_size = token_size * 4        # ~2000 chars
        self.chunk_overlap = token_overlap * 4  # ~250 chars
        self.separators = separators or settings.chunk_separators

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────

    def chunk_text(self, text: str, source: str = "unknown") -> list[Chunk]:
        """
        Split a single text into overlapping chunks.
        Returns list of Chunk objects with metadata.
        """
        if not text or not text.strip():
            return []

        # Clean text before chunking
        text = self._clean_text(text)

        # Recursive split
        raw_chunks = self._recursive_split(text, self.separators)
        raw_count = len(raw_chunks)

        # Add overlap between chunks
        overlapped = self._add_overlap(raw_chunks)

        # Build Chunk objects with metadata
        chunks = []
        for i, chunk_text in enumerate(overlapped):
            if chunk_text.strip():  # skip empty chunks
                chunks.append(
                    Chunk(
                        text=chunk_text.strip(),
                        source=source,
                        chunk_index=i,
                    )
                )

        logger.info(
            f"Chunked '{source}': {len(text)} chars → {raw_count} raw → {len(chunks)} final chunks "
            f"(size={self.chunk_size}, overlap={self.chunk_overlap})"
        )
        return chunks

    def chunk_documents(
        self, documents: list[dict[str, str]]
    ) -> list[Chunk]:
        """
        Chunk multiple documents. Each doc is {"text": ..., "source": ...}.
        Returns flat list of all chunks from all documents.
        """
        all_chunks = []
        for doc in documents:
            text = doc.get("text", "")
            source = doc.get("source", "unknown")
            chunks = self.chunk_text(text, source=source)
            all_chunks.extend(chunks)

        logger.info(
            f"Chunked {len(documents)} documents → {len(all_chunks)} total chunks"
        )
        return all_chunks

    # ─────────────────────────────────────────
    # Recursive Splitting
    # ─────────────────────────────────────────

    def _recursive_split(
        self, text: str, separators: list[str]
    ) -> list[str]:
        """
        Recursively split text using the separator hierarchy.
        Try the first separator; if any resulting chunk is still too large,
        split it again with the next separator.
        """
        if len(text) <= self.chunk_size:
            return [text]

        if not separators:
            # No separators left — hard split at chunk_size
            return self._hard_split(text)

        separator = separators[0]
        remaining_separators = separators[1:]

        # Split by current separator
        if separator == ". ":
            # Sentence boundary — use regex to handle abbreviations better
            parts = re.split(r'(?<=[.!?])\s+', text)
        else:
            parts = text.split(separator)

        # Merge small parts together, split large parts further
        chunks = []
        current = ""

        for part in parts:
            # If adding this part would exceed chunk_size
            test = current + separator + part if current else part

            if len(test) <= self.chunk_size:
                current = test
            else:
                # Save current chunk if it has content
                if current:
                    chunks.append(current)

                # If this single part is too large, recurse with next separator
                if len(part) > self.chunk_size:
                    sub_chunks = self._recursive_split(
                        part, remaining_separators
                    )
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = part

        # Don't forget the last chunk
        if current:
            chunks.append(current)

        return chunks

    def _hard_split(self, text: str) -> list[str]:
        """Last resort: split at exact character boundaries."""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunks.append(text[i : i + self.chunk_size])
        return chunks

    # ─────────────────────────────────────────
    # Overlap
    # ─────────────────────────────────────────

    def _add_overlap(self, chunks: list[str]) -> list[str]:
        """
        Add overlap between consecutive chunks.
        Each chunk gets the tail of the previous chunk prepended.
        Enforces chunk_size cap after overlap to prevent oversized chunks.
        """
        if len(chunks) <= 1 or self.chunk_overlap <= 0:
            return chunks

        overlapped = [chunks[0][:self.chunk_size]]  # Cap first chunk too

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            current = chunks[i]

            # Get the tail of the previous chunk as overlap
            overlap_text = prev[-self.chunk_overlap :]

            # Find a clean break point in the overlap (sentence or word boundary)
            clean_start = overlap_text.find(". ")
            if clean_start != -1 and clean_start < len(overlap_text) * 0.5:
                overlap_text = overlap_text[clean_start + 2 :]
            else:
                space_start = overlap_text.find(" ")
                if space_start != -1:
                    overlap_text = overlap_text[space_start + 1 :]

            merged = overlap_text + " " + current
            # Enforce chunk_size cap after overlap addition
            if len(merged) > self.chunk_size:
                truncated = merged[:self.chunk_size]
                last_sentence = truncated.rfind(". ")
                if last_sentence > len(truncated) * 0.5:
                    merged = truncated[:last_sentence + 1]
                else:
                    last_space = truncated.rfind(" ")
                    if last_space != -1:
                        merged = truncated[:last_space]
                    else:
                        merged = truncated
            overlapped.append(merged)

        return overlapped

    # ─────────────────────────────────────────
    # Text Cleaning
    # ─────────────────────────────────────────

    def _clean_text(self, text: str) -> str:
        """Clean raw text before chunking."""
        # Collapse multiple newlines into double newline (paragraph break)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Collapse multiple spaces
        text = re.sub(r"[ \t]{2,}", " ", text)
        # Remove null bytes and other control characters
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return text.strip()

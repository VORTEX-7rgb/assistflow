"""
RapidRAG — rag_engine.py  (FIXED)

BUGS FIXED vs previous version:
  1. GROQ_API_KEY lookup: os.getenv("GROQ_API_KEY") misses keys loaded
     by pydantic-settings from .env file (pydantic loads them into settings
     object but doesn't put them in the process env). Now reads from settings
     object FIRST, falls back to os.getenv.

  2. Added explicit error message when API key is missing — previously failed
     silently with a 401 from Groq that was hard to debug.
"""

import os
import json
import logging
import httpx

from config import (
    settings,
    LLM_PROVIDERS,
    RAGResponse,
    Chunk,
    ClientConfig,
    LEAD_INTENTS,
)
from embedder import Embedder
from utils import normalize_query, deduplicate_sentences, safe_json_parse, truncate_context
from lead_dispatcher import LeadDispatcher, LeadEvent

logger = logging.getLogger(__name__)


class RAGEngine:
    """
    Unified RAG brain. Owns the entire query lifecycle:
      1. Normalize query
      2. Check cache
      3. Retrieve relevant chunks from vector store
      4. Score & filter with dynamic threshold
      5. Deduplicate context
      6. Build prompt with client-specific system prompt
      7. Call LLM (JSON-enforced output)
      8. Parse structured response
      9. Cache result
    """

    def __init__(self, embedder: Embedder, client_manager, cache=None):
        self.embedder = embedder
        self.client_manager = client_manager
        self.cache = cache

        self.llm_client = httpx.AsyncClient(
            timeout=settings.llm_timeout,
            limits=httpx.Limits(
                max_connections=settings.llm_max_connections,
                max_keepalive_connections=settings.llm_keepalive_connections,
            ),
        )
        # main.py sets lead_dispatcher and session_manager explicitly now
        
        # NOTE: Session manager should be passed in or instantiated. We actually need it to be global to share state.
        # Main.py creates RagEngine, we'll import it or pass it. Let's pass it in via init.
        # To avoid breaking existing calls temporarily, we'll try to get it from kwargs or global.
        self.session_manager = None
        self.lead_dispatcher = None

    # ─────────────────────────────────────────
    # Main Entry Point
    # ─────────────────────────────────────────

    async def answer(self, client_id: str, query: str, session_id: str = "") -> RAGResponse:
        """Full RAG pipeline."""
        client_config = self.client_manager.load_client(client_id)
        normalized = normalize_query(query)

        # Step 1: Check cache
        if self.cache:
            cached = self.cache.get(client_id, normalized)
            if cached is not None:
                logger.info(f"[{client_id}] Cache hit: {normalized[:50]}")
                return cached

        # Step 2: Retrieve
        chunks = await self.retrieve(client_id, normalized, client_config)

        # Step 3: No context chunks fallback handled entirely by LLM
        if not chunks:
            logger.info(f"[{client_id}] No chunks found, LLM will answer generally.")
        
        # Step 3.5: Fetch session state variables
        session_data = self.session_manager.get(session_id) if (self.session_manager and session_id) else {}
        collected_name = session_data.get("user_name", "")
        collected_phone = session_data.get("user_phone", "")
        collected_email = session_data.get("user_email", "")
        session_history = session_data.get("messages", [])

        # Step 4: Generate
        response = await self.generate(normalized, chunks, client_config, collected_name, collected_phone, collected_email, session_history)

        # Step 4.5: Update Session Manager
        if self.session_manager and session_id:
            self.session_manager.update(session_id, client_id, query, response, client_config)
            
        # Old Instant Lead dispatching REMOVED. Let Session Manager take over.
        if response.lead:
            logger.info(f"[{client_id}] Lead intent identified, but dispatch will wait for session inactivity.")

        # Step 5: Cache
        if self.cache:
            self.cache.set(client_id, normalized, response)

        return response

    # ─────────────────────────────────────────
    # Retrieval
    # ─────────────────────────────────────────

    async def retrieve(
        self, client_id: str, query: str, config: ClientConfig
    ) -> list[Chunk]:
        """Embed query → vector search → dynamic threshold → deduplicate."""
        collection = self.client_manager.get_vector_store(client_id)

        if collection.count() == 0:
            logger.warning(f"[{client_id}] Vector store is empty — ingest data first")
            return []

        query_embedding = await self.embedder.embed_query(query)

        top_k = min(config.top_k or settings.default_top_k, collection.count())
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "distances", "metadatas"],
        )

        documents = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if not documents:
            logger.warning(f"[{client_id}] No vectors returned from query")
            return []

        scores = [1.0 - d for d in distances]
        threshold = self._dynamic_threshold(scores)
        max_score = max(scores) if scores else 0.0

        logger.info(
            f"[{client_id}] Retrieval: top_score={max_score:.3f}, "
            f"threshold={threshold:.3f}, candidates={len(documents)}"
        )

        if max_score < threshold:
            logger.warning(
                f"[{client_id}] All scores below threshold "
                f"({max_score:.3f} < {threshold:.3f})"
            )
            return []

        chunks = []
        for doc, score, meta in zip(documents, scores, metadatas):
            if score >= threshold:
                chunks.append(
                    Chunk(
                        text=doc,
                        source=meta.get("source", "unknown"),
                        chunk_index=meta.get("chunk_index", 0),
                        score=score,
                    )
                )

        chunks.sort(key=lambda c: c.score, reverse=True)
        chunks = self._deduplicate_context(chunks)
        return chunks

    # ─────────────────────────────────────────
    # Generation
    # ─────────────────────────────────────────

    async def generate(
        self, query: str, chunks: list[Chunk], config: ClientConfig,
        collected_name: str = "", collected_phone: str = "", collected_email: str = "", session_history: list = None
    ) -> RAGResponse:
        """Build prompt → call LLM → parse JSON response."""
        system_prompt = self.client_manager.get_prompt(config.client_id)
        context_str = self._build_context(chunks)
        context_str = truncate_context(context_str, settings.max_context_tokens)
        prompt = self._build_prompt(
            query, context_str, system_prompt, config,
            collected_name, collected_phone, collected_email
        )

        messages = [
            {"role": "system", "content": prompt},
        ]
        
        if session_history:
            for msg in session_history[-10:]:
                role = "assistant" if msg["role"] == "bot" else msg["role"]
                messages.append({"role": role, "content": msg["content"]})
                
        messages.append({"role": "user", "content": query})

        raw_response = await self._call_llm(config.llm_model, messages)
        parsed = self._parse_llm_json(raw_response)

        intent = parsed.get("intent", "unknown")
        is_lead = parsed.get("lead", False)
        if intent in LEAD_INTENTS:
            is_lead = True

        max_score = max((c.score for c in chunks), default=0.0)
        sources = list({c.source for c in chunks})

        user_name = parsed.get("user_name", "") or ""
        user_phone = parsed.get("user_phone", "") or ""
        user_email = parsed.get("user_email", "") or ""
        collected = bool(user_name and user_phone)

        return RAGResponse(
            reply=parsed.get("reply", raw_response),
            intent=intent,
            lead=is_lead,
            confidence=round(max_score, 3),
            sources=sources,
            user_name=user_name,
            user_phone=user_phone,
            user_email=user_email,
            collected=collected
        )

    # ─────────────────────────────────────────
    # LLM Call  (FIX: API key lookup)
    # ─────────────────────────────────────────

    async def _call_llm(self, model_string: str, messages: list) -> str:
        """Call LLM via OpenAI-compatible API with fallback support."""
        models_to_try = getattr(settings, "fallback_llm_models", [model_string])
        
        last_exception = None
        for current_model_string in models_to_try:
            try:
                provider, model = current_model_string.split("/", 1)
                provider_config = LLM_PROVIDERS.get(provider)

                if not provider_config:
                    raise ValueError(f"Unknown LLM provider: {provider}")

                api_key = None
                key_env = provider_config.get("key_env")
                if key_env:
                    settings_attr = key_env.lower()
                    api_key = (getattr(settings, settings_attr, None) or os.getenv(key_env))

                if key_env and not api_key:
                    raise ValueError(f"Missing API key for provider '{provider}'")

                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": settings.llm_max_tokens,
                    "temperature": 0.6,
                }

                response = await self.llm_client.post(
                    f"{provider_config['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=settings.llm_timeout,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

            except httpx.TimeoutException as e:
                logger.warning(f"LLM timeout on {current_model_string}, falling back...")
                last_exception = e
                continue
            except httpx.HTTPStatusError as e:
                logger.warning(f"LLM HTTP error {e.response.status_code} on {current_model_string}, falling back...")
                last_exception = e
                continue
            except Exception as e:
                logger.warning(f"LLM unexpected error on {current_model_string}: {e}, falling back...")
                last_exception = e
                continue

        # If all models failed
        logger.error("All fallback LLM models failed.")
        raise last_exception or Exception("All LLM models failed")

    # ─────────────────────────────────────────
    # Prompt Building
    # ─────────────────────────────────────────

    def _build_prompt(
        self, query: str, context: str, system_prompt: str, config: ClientConfig,
        collected_name: str = "", collected_phone: str = "", collected_email: str = ""
    ) -> str:
        prompt = system_prompt.replace("{business_name}", config.business_name)
        prompt = prompt.replace("{business_type}", config.business_type)
        prompt = prompt.replace("{contact_email}", config.contact_email)
        prompt = prompt.replace("{contact_phone}", config.contact_phone)
        prompt = prompt.replace("{context}", context)
        prompt = prompt.replace("{query}", query)
        prompt = prompt.replace("{collected_name}", collected_name)
        prompt = prompt.replace("{collected_phone}", collected_phone)
        prompt = prompt.replace("{collected_email}", collected_email)
        return prompt

    def _build_context(self, chunks: list[Chunk]) -> str:
        parts = []
        for chunk in chunks:
            parts.append(f"--- Source: {chunk.source} ---\n{chunk.text}")
        return deduplicate_sentences("\n\n".join(parts))

    # ─────────────────────────────────────────
    # Dynamic Threshold
    # ─────────────────────────────────────────

    def _dynamic_threshold(self, scores: list[float]) -> float:
        if not scores:
            return settings.similarity_floor
        avg_score = sum(scores) / len(scores)
        return max(
            avg_score * settings.dynamic_threshold_factor,
            settings.similarity_floor,
        )

    # ─────────────────────────────────────────
    # Context Deduplication
    # ─────────────────────────────────────────

    def _deduplicate_context(self, chunks: list[Chunk]) -> list[Chunk]:
        if len(chunks) <= 1:
            return chunks
        unique = [chunks[0]]
        seen = set(chunks[0].text.lower().split(". "))
        for chunk in chunks[1:]:
            chunk_sents = set(chunk.text.lower().split(". "))
            overlap = len(chunk_sents & seen)
            total = len(chunk_sents) if chunk_sents else 1
            if overlap / total < 0.7:
                unique.append(chunk)
                seen.update(chunk_sents)
        return unique

    # ─────────────────────────────────────────
    # JSON Parsing
    # ─────────────────────────────────────────

    def _parse_llm_json(self, raw_response: str) -> dict:
        return safe_json_parse(raw_response)

    # ─────────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────────

    async def close(self):
        await self.llm_client.aclose()
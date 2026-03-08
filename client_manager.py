"""
RapidRAG — client_manager.py
Multi-client loader, router, and LRU eviction.
Manages per-client configs, prompts, and vector stores.
"""

import os
import json
import logging
from collections import OrderedDict

import chromadb

from config import settings, ClientConfig

logger = logging.getLogger(__name__)

# Default prompt template (used when client has no custom prompt)
# Lives in prompts/default_system.txt — this is the hardcoded fallback only
_DEFAULT_PROMPT_FALLBACK = """You are a helpful and conversational assistant for {business_name}, a {business_type}.

STRICT RULES:
1. ONLY answer using the CONTEXT provided below. Never invent information.
2. If the user asks general conversational queries (e.g., greetings, small talk) or something outside the provided context, respond normally directly concisely using your built in generic human tone. Do NOT over explain.
3. NEVER invent prices, services, phone numbers, addresses, or doctor names.
4. Be professional, concise, and helpful.
5. IF the answer is not in the context, politely state that you don't have that information and provide the contact details: Email: {contact_email} | Phone: {contact_phone}. DO NOT hallucinate answers.
6. If asked about booking/appointment, encourage contacting the business directly via the contact details provided above.

CONTEXT:
{context}

USER QUESTION: {query}

Respond with ONLY valid JSON, no other text:
{"reply": "your answer here", "intent": "one of: pricing_query|service_inquiry|appointment_request|faq|contact_request|complaint|general|unknown", "lead": true or false, "user_name": "...", "user_phone": "...", "requirement": "customer need...", "budget": "budget..."}"""


class ClientManager:
    """
    Manages per-client configuration, prompts, and ChromaDB collections.
    Features:
    - LRU eviction (Fix 6): max N clients loaded simultaneously
    - Cold start preload (Fix 5): warm recent clients at startup
    - Hot-reload: config changes detected via file mtime
    - Embedding versioning: tracks model used to build vectors
    """

    def __init__(self, base_path: str, max_loaded: int | None = None):
        self.base_path = base_path
        self.max_loaded = max_loaded or settings.max_clients_loaded

        # LRU-ordered caches
        self._cache: OrderedDict[str, tuple[ClientConfig, float]] = OrderedDict()
        self._collections: OrderedDict[str, chromadb.Collection] = OrderedDict()
        self._chroma_clients: OrderedDict[str, chromadb.PersistentClient] = OrderedDict()

        # Cached client list (avoid repeated os.listdir)
        self._client_list_cache: list[str] | None = None
        self._client_list_mtime: float = 0.0

        # Ensure base directory exists
        os.makedirs(base_path, exist_ok=True)

    # ─────────────────────────────────────────
    # Client Config Loading
    # ─────────────────────────────────────────

    def load_client(self, client_id: str) -> ClientConfig:
        """
        Load client config from disk, with LRU caching.
        Auto-reloads if config.json has been modified.
        """
        config_path = os.path.join(self.base_path, client_id, "config.json")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Client config not found: {client_id}")

        current_mtime = os.path.getmtime(config_path)

        # Check cache — refresh LRU position across ALL caches
        if client_id in self._cache:
            cached_config, cached_mtime = self._cache[client_id]
            if cached_mtime >= current_mtime:
                self._cache.move_to_end(client_id)
                # Keep collections in sync with config LRU
                if client_id in self._collections:
                    self._collections.move_to_end(client_id)
                if client_id in self._chroma_clients:
                    self._chroma_clients.move_to_end(client_id)
                return cached_config
            # Config changed on disk — reload
            logger.info(f"[{client_id}] Config changed, reloading")

        # Evict if at capacity
        self._evict_lru()

        # Load from disk
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        config = ClientConfig(**data)
        self._cache[client_id] = (config, current_mtime)
        self._cache.move_to_end(client_id)
        return config

    # ─────────────────────────────────────────
    # Prompt Loading
    # ─────────────────────────────────────────

    def get_prompt(self, client_id: str) -> str:
        """
        Load client-specific system prompt.
        Falls back to default if no custom prompt exists.
        """
        prompt_path = os.path.join(self.base_path, client_id, "prompt.txt")

        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read().strip()

        # Fallback: check global default
        default_path = os.path.join(settings.project_root, "prompts", "default_system.txt")
        if os.path.exists(default_path):
            with open(default_path, "r", encoding="utf-8") as f:
                return f.read().strip()

        return _DEFAULT_PROMPT_FALLBACK

    # ─────────────────────────────────────────
    # Vector Store Access
    # ─────────────────────────────────────────

    def get_vector_store(self, client_id: str) -> chromadb.Collection:
        """
        Return ChromaDB collection for this client. LRU managed.
        Creates collection if it doesn't exist yet.
        """
        if client_id in self._collections:
            self._collections.move_to_end(client_id)
            return self._collections[client_id]

        # Evict if at capacity
        self._evict_lru()

        vectors_path = os.path.join(self.base_path, client_id, "vectors")
        os.makedirs(vectors_path, exist_ok=True)

        chroma_client = chromadb.PersistentClient(path=vectors_path)
        collection = chroma_client.get_or_create_collection(
            name=f"client_{client_id}",
            metadata={"hnsw:space": "cosine"},
        )

        self._chroma_clients[client_id] = chroma_client
        self._collections[client_id] = collection
        self._collections.move_to_end(client_id)

        logger.debug(
            f"[{client_id}] Vector store loaded: {collection.count()} vectors"
        )
        return collection

    # ─────────────────────────────────────────
    # Client Creation
    # ─────────────────────────────────────────

    def create_client(
        self,
        client_id: str,
        business_name: str,
        business_type: str = "business",
        website_url: str = "",
        contact_email: str = "",
        contact_phone: str = "",
        owner_email: str = "",
        n8n_webhook_url: str = "",
        bot_name: str = "Assistant",
        logo_url: str = "",
        instagram_page_id: str = "",
        manychat_api_key: str = "",
        instagram_access_token: str = "",
        instagram_channel: str = "",
    ) -> None:
        """
        Create full client directory structure + default config.
        Raises FileExistsError if client already exists.
        """
        client_dir = os.path.join(self.base_path, client_id)
        if os.path.exists(client_dir):
            raise FileExistsError(f"Client directory already exists: {client_id}")

        # Create directory structure
        os.makedirs(os.path.join(client_dir, "vectors"), exist_ok=True)
        os.makedirs(os.path.join(client_dir, "documents"), exist_ok=True)

        # Write config.json
        config = ClientConfig(
            client_id=client_id,
            business_name=business_name,
            business_type=business_type,
            website_url=website_url,
            contact_email=contact_email,
            n8n_webhook_url=n8n_webhook_url,
            owner_email=owner_email,
            bot_name=bot_name,
            logo_url=logo_url,
            instagram_page_id=instagram_page_id,
            manychat_api_key=manychat_api_key,
            instagram_access_token=instagram_access_token,
            instagram_channel=instagram_channel,
            prompt_version=1,
        )
        config_path = os.path.join(client_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2)

        # Write default prompt (read from file if exists, else use fallback)
        prompt_path = os.path.join(client_dir, "prompt.txt")
        default_path = os.path.join(settings.project_root, "prompts", "default_system.txt")
        if os.path.exists(default_path):
            import shutil
            shutil.copy2(default_path, prompt_path)
        else:
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(_DEFAULT_PROMPT_FALLBACK)

        # Write embedding version file
        version_path = os.path.join(client_dir, "vectors", "embedding_model.txt")
        with open(version_path, "w", encoding="utf-8") as f:
            f.write(settings.embedding_model)

        # Initialize empty ChromaDB collection
        self.get_vector_store(client_id)

        logger.info(
            f"Client created: {client_id} "
            f"({business_name}, {business_type})"
        )

    def update_client_status(self, client_id: str, is_active: bool) -> None:
        """Update the active status (subscription toggle) for a client."""
        config_path = os.path.join(self.base_path, client_id, "config.json")
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Client config not found: {client_id}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["is_active"] = is_active
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        logger.info(f"Client {client_id} subscription status updated to active={is_active}")

    # ─────────────────────────────────────────
    # Client Queries
    # ─────────────────────────────────────────

    def client_exists(self, client_id: str) -> bool:
        """Check if a client directory exists."""
        config_path = os.path.join(self.base_path, client_id, "config.json")
        return os.path.exists(config_path)

    def list_clients(self) -> list[str]:
        """List all registered client IDs (cached for 30s)."""
        import time as _time
        now = _time.time()
        if self._client_list_cache is not None and (now - self._client_list_mtime) < 30:
            return self._client_list_cache

        if not os.path.exists(self.base_path):
            self._client_list_cache = []
            self._client_list_mtime = now
            return []

        result = [
            d for d in os.listdir(self.base_path)
            if os.path.isfile(os.path.join(self.base_path, d, "config.json"))
        ]
        self._client_list_cache = result
        self._client_list_mtime = now
        return result

    # ─────────────────────────────────────────
    # LRU Eviction (Fix 6)
    # ─────────────────────────────────────────

    def _evict_lru(self) -> None:
        """Unload least-recently-used client if over capacity."""
        while max(len(self._cache), len(self._collections), len(self._chroma_clients)) >= self.max_loaded:
            # Pick an ID to evict from the oldest in any cache
            evicted_id = None
            if len(self._cache) >= self.max_loaded:
                evicted_id = next(iter(self._cache))
            elif len(self._collections) >= self.max_loaded:
                evicted_id = next(iter(self._collections))
            elif len(self._chroma_clients) >= self.max_loaded:
                evicted_id = next(iter(self._chroma_clients))
            
            if not evicted_id:
                break
                
            self._cache.pop(evicted_id, None)
            self._collections.pop(evicted_id, None)
            self._chroma_clients.pop(evicted_id, None)
            logger.info(f"LRU evicted client: {evicted_id}")

    # ─────────────────────────────────────────
    # Cold Start Preload (Fix 5)
    # ─────────────────────────────────────────

    def preload_active(self, count: int = 5) -> None:
        """
        Pre-load the N most recently modified clients at startup.
        Eliminates cold-start lag for demos.
        Race-condition safe: catches all errors per client.
        """
        all_clients = self.list_clients()
        if not all_clients:
            logger.info("No clients to preload")
            return

        # Sort by config.json modification time (most recent first)
        def get_mtime(cid: str) -> float:
            path = os.path.join(self.base_path, cid, "config.json")
            try:
                return os.path.getmtime(path)
            except OSError:
                return 0.0

        recent = sorted(all_clients, key=get_mtime, reverse=True)[:count]

        loaded = 0
        for cid in recent:
            try:
                self.load_client(cid)
                self.get_vector_store(cid)
                loaded += 1
                logger.info(f"Preloaded client: {cid}")
            except Exception as e:
                logger.warning(f"Failed to preload {cid}: {e}")
        logger.info(f"Preloaded {loaded}/{len(recent)} clients")

"""
RapidRAG — Production-grade multi-tenant RAG SaaS engine.

Modules:
    config      — Global settings, models, provider registry
    main        — FastAPI app entry point
    rag_engine  — Unified RAG brain (retrieve + generate + answer)
    embedder    — Embedding pipeline (single + batch)
    chunker     — Recursive text chunking with overlap
    scraper     — Async web scraper with smart filtering
    ingestion   — Ingestion orchestrator (scrape/PDF → chunk → embed → store)
    client_manager — Multi-client loader with LRU eviction
    cache       — Two-tier response cache (memory + disk)
    utils       — Query normalization, dedup, JSON parsing
    errors      — Custom exception hierarchy
    logger      — Structured JSON logging
    middleware  — Request tracking, rate limiting
"""

__version__ = "1.0.0"
__app_name__ = "RapidRAG"

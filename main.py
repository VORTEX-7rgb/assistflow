"""
RapidRAG — main.py  (FIXED)
FastAPI application entry point.

BUGS FIXED vs previous version:
  1. Duplicate client_status route removed (was defined twice)
  2. app.mount("/static") moved BEFORE all routes were fine, but
     /dashboard and /ingest/text were defined AFTER the mount — moved them up
  3. WebsiteIngestRequest URL fix moved to field_validator (model_validate override
     doesn't fire during FastAPI request parsing)
  4. ingest/text now properly calls the ingestion pipeline with lock
"""

import os
import sys
import time
import logging
import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Query, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    settings,
    RAGResponse,
    ChatRequest,
    WebsiteIngestRequest,
    ClientCreateRequest,
)
from embedder import Embedder
from rag_engine import RAGEngine
from client_manager import ClientManager
from chunker import Chunker
from scraper import WebScraper
from ingestion import IngestionPipeline
from cache import ResponseCache
from errors import RapidRAGError, IngestionLockError
from logger import setup_logging, structured_logger
from middleware import (
    RequestTrackingMiddleware,
    RateLimitMiddleware,
    rapidrag_error_handler,
)
from report_engine import ReportEngine
from scheduler import ReportScheduler
from session_manager import SessionManager
from lead_dispatcher import LeadDispatcher
from instagram_handler import InstagramHandler

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

setup_logging(debug=settings.debug)
logger = logging.getLogger("rapidrag")


# ─────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    embedder.load_model()
    logger.info("Embedding model loaded")
    client_manager.preload_active(settings.preload_count)
    logger.info(f"Preloaded {settings.preload_count} most recent clients")
    
    report_scheduler.start()
    session_manager.start()
    
    logger.info("RapidRAG is ready 🚀")
    yield
    logger.info("Shutting down RapidRAG...")
    await session_manager.stop()
    await lead_dispatcher.close()
    await instagram_handler_instance.close()
    report_scheduler.stop()
    await report_engine.close()
    await rag_engine.close()
    logger.info("Shutdown complete")


# ─────────────────────────────────────────────
# Core Components
# ─────────────────────────────────────────────

embedder        = Embedder()
client_manager  = ClientManager(base_path=settings.base_client_path)
response_cache  = ResponseCache()
scraper         = WebScraper()
chunker         = Chunker()

rag_engine = RAGEngine(
    embedder=embedder,
    client_manager=client_manager,
    cache=response_cache,
)

lead_dispatcher = LeadDispatcher()
session_manager = SessionManager(lead_dispatcher=lead_dispatcher, client_manager=client_manager)
instagram_handler_instance = InstagramHandler(
    rag_engine=rag_engine,
    session_manager=session_manager,
    client_manager=client_manager,
)
rag_engine.session_manager = session_manager
rag_engine.lead_dispatcher = lead_dispatcher
lead_dispatcher.set_llm_summarizer(rag_engine)

report_engine = ReportEngine()
report_scheduler = ReportScheduler(client_manager, report_engine)

ingestion_pipeline = IngestionPipeline(
    scraper=scraper,
    chunker=chunker,
    embedder=embedder,
    client_manager=client_manager,
)


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Production-grade multi-tenant RAG SaaS engine",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")] if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestTrackingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.exception_handler(RapidRAGError)(rapidrag_error_handler)


# ─────────────────────────────────────────────
# Admin Auth Dependency
# ─────────────────────────────────────────────

async def require_admin_key(authorization: str = Header(None)):
    """Reject requests without a valid Bearer token on admin routes."""
    if not settings.admin_api_key:
        return  # No key configured = auth disabled (local dev)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header. Use: Bearer <ADMIN_API_KEY>")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ─────────────────────────────────────────────
# Text Ingest Model (defined here so it's above the route)
# ─────────────────────────────────────────────

class TextIngestRequest(BaseModel):
    text: str
    source: str = "manual-text"


# ─────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "clients_loaded": len(client_manager._cache),
        "embedding_model": embedder.model_name,
        "cache": response_cache.stats(),
    }


# ─────────────────────────────────────────────
# Dashboard  (MUST be before app.mount)
# ─────────────────────────────────────────────

@app.get("/dashboard")
async def serve_dashboard():
    """Serve the operator dashboard."""
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    return FileResponse(dashboard_path, media_type="text/html")


# ─────────────────────────────────────────────
# Client Management
# ─────────────────────────────────────────────

@app.post("/api/v1/clients")
async def create_client(request: ClientCreateRequest):
    """Onboard a new client."""
    try:
        client_manager.create_client(
            client_id=request.client_id,
            business_name=request.business_name,
            business_type=request.business_type,
            website_url=request.website_url,
            contact_email=request.contact_email,
            contact_phone=request.contact_phone,
            owner_email=request.owner_email,
            n8n_webhook_url=request.n8n_webhook_url,
            bot_name=request.bot_name,
            logo_url=request.logo_url,
            instagram_page_id=request.instagram_page_id,
            manychat_api_key=request.manychat_api_key,
        )
        logger.info(f"Client created: {request.client_id}")
        return {"status": "created", "client_id": request.client_id}
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Client '{request.client_id}' already exists")
    except Exception as e:
        logger.error(f"Client creation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ToggleStatusRequest(BaseModel):
    is_active: bool

@app.post("/api/v1/{client_id}/toggle-status", dependencies=[Depends(require_admin_key)])
async def toggle_client_status(client_id: str, request: ToggleStatusRequest):
    """Toggle the client's active subscription status."""
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    try:
        client_manager.update_client_status(client_id, request.is_active)
        response_cache.invalidate_client(client_id)
        return {"status": "success", "is_active": request.is_active}
    except Exception as e:
        logger.error(f"Error toggling status for {client_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/v1/upload-logo")
async def upload_logo(file: UploadFile = File(...)):
    """Upload a logo image and return its public URL."""
    try:
        logos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logos")
        os.makedirs(logos_dir, exist_ok=True)
        
        # Make filename safe
        safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._-").strip()
        if not safe_filename:
            safe_filename = f"logo_{int(time.time())}.png"
            
        file_path = os.path.join(logos_dir, safe_filename)
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
            
        # Return URL
        return {"url": f"/static/logos/{safe_filename}", "filename": safe_filename}
    except Exception as e:
        logger.error(f"Logo upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/clients")
async def list_clients():
    """List all registered client IDs."""
    clients = client_manager.list_clients()
    return {"clients": clients, "count": len(clients)}

@app.get("/api/v1/{client_id}/widget-config")
async def widget_config(client_id: str):
    """
    Returns dynamic widget configuration based on the business type.
    This allows the widget.js to show tailored greetings and chips.
    """
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        config = client_manager.load_client(client_id)
        
        # Default fallback
        greeting = "Hi there! 👋 How can I help you today?"
        chips = ["What services do you offer?", "How much does it cost?", "How do I get started?"]
        
        business_type = config.business_type.lower()
        
        if "school" in business_type or "education" in business_type:
            greeting = "Hi! 👋 Looking for admission info, fees, or courses? I can help instantly."
            chips = ["What are the fees?", "Admission process?", "What courses do you offer?"]
        elif "travel" in business_type or "agency" in business_type:
            greeting = "Hello! Planning a trip? Ask me about packages, pricing, or destinations! ✈️"
            chips = ["Show me holiday packages", "What's included in the price?", "How do I book?"]
        elif "restaurant" in business_type or "food" in business_type:
            greeting = "Hi there! 😊 Ask me about our menu, timings, reservations, or special offers."
            chips = ["See the menu", "Table reservation?", "What are your timings?"]
        elif "real estate" in business_type or "property" in business_type:
            greeting = "Welcome! Looking to buy, rent, or invest? Tell me what you're looking for. 🏠"
            chips = ["Properties available?", "What's the price range?", "How do I schedule a visit?"]
            
        return {
            "business_name": config.business_name,
            "greeting": greeting,
            "chips": chips,
            "logo_url": config.logo_url if hasattr(config, 'logo_url') else ""
        }
    except Exception as e:
        logger.error(f"Error serving widget config: {e}", exc_info=True)
        # Fallback if config loading fails
        return {
            "business_name": "Assistant",
            "greeting": "Hi there! 👋 How can I help you today?",
            "chips": ["What services do you offer?", "How do I get started?"],
            "logo_url": ""
        }


@app.get("/api/v1/{client_id}/status")
async def client_status(client_id: str):
    """
    Get full status of a client — used by the dashboard.
    FIX: was duplicated (defined twice). Second definition had more fields.
    Now merged into one complete definition.
    """
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")

    try:
        config = client_manager.load_client(client_id)
        collection = client_manager.get_vector_store(client_id)
        vector_count = collection.count()
    except Exception:
        vector_count = 0
        config = None

    return {
        "client_id": client_id,
        "is_active": config.is_active if config else False,
        "business_name": config.business_name if config else client_id,
        "business_type": config.business_type if config else "",
        "llm_model": config.llm_model if config else settings.default_llm_model,
        "embedding_model": embedder.model_name,
        "top_k": config.top_k if config else settings.default_top_k,
        "vector_count": vector_count,
        "ingesting": ingestion_pipeline.is_ingesting(client_id),
        "prompt_version": config.prompt_version if config else 1,
    }


# ─────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────

@app.post("/api/v1/{client_id}/chat", response_model=RAGResponse)
async def chat(client_id: str, request: ChatRequest):
    start_time = time.time()
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    try:
        config = client_manager.load_client(client_id)
        if not getattr(config, "is_active", True):
            return RAGResponse(
                reply="This virtual assistant is currently unavailable.",
                intent="unknown",
                confidence=1.0,
                logic_steps=["Subscription inactive"],
                sources=[],
                lead=False,
                cache_hit=False
            )
            
        response = await rag_engine.answer(client_id, request.query, request.session_id)
        latency_ms = int((time.time() - start_time) * 1000)
        
        structured_logger.log_chat_request(
            client_id=client_id,
            query=request.query,
            reply=response.reply,
            intent=response.intent,
            lead=response.lead,
            confidence=response.confidence,
            sources=response.sources,
            latency_ms=latency_ms,
            cache_hit=response.cache_hit,
            prompt_version=client_manager.load_client(client_id).prompt_version,
            embedding_model=settings.embedding_model,
        )

        logger.info(
            f"[{client_id}] Chat: intent={response.intent}, "
            f"confidence={response.confidence}, latency={latency_ms}ms"
        )
        return response
    except Exception as e:
        logger.error(f"[{client_id}] Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred")

# ─────────────────────────────────────────────
# Manually Triggered Admin Routes
# ─────────────────────────────────────────────

@app.post("/api/v1/{client_id}/report/trigger", dependencies=[Depends(require_admin_key)])
async def trigger_weekly_report(client_id: str):
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    try:
        config = client_manager.load_client(client_id)
        await report_engine.send_weekly_report(client_id, config)
        return {"status": "success", "message": "Weekly report triggered"}
    except Exception as e:
        logger.error(f"Failed to trigger weekly report: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# Ingestion — Website
# ─────────────────────────────────────────────

@app.post("/api/v1/{client_id}/ingest/website", dependencies=[Depends(require_admin_key)])
async def ingest_website(client_id: str, request: WebsiteIngestRequest):
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    try:
        result = await ingestion_pipeline.ingest_website(client_id, request.url)
        response_cache.invalidate_client(client_id)
        return result.model_dump()
    except IngestionLockError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"[{client_id}] Ingestion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ────# ─────────────────────────────────────────
# Ingestion — Generic File (PDF, DOCX, CSV, XLSX)
# ─────────────────────────────────────────

@app.post("/api/v1/{client_id}/ingest/file", dependencies=[Depends(require_admin_key)])
@app.post("/api/v1/{client_id}/ingest/pdf", dependencies=[Depends(require_admin_key)])  # Backwards compatibility
async def ingest_file_endpoint(client_id: str, file: UploadFile = File(...)):
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    try:
        import tempfile
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in [".pdf", ".docx", ".xlsx", ".xls", ".csv"]:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        try:
            result = await ingestion_pipeline.ingest_file(
                client_id, tmp_path, filename=file.filename or f"upload{ext}"
            )
            response_cache.invalidate_client(client_id)
            return result.model_dump()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except HTTPException:
        raise
    except IngestionLockError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"[{client_id}] File ingestion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Ingestion — Text  (FIX: was AFTER app.mount so it was dead)
# ─────────────────────────────────────────────

@app.post("/api/v1/{client_id}/ingest/text", dependencies=[Depends(require_admin_key)])
async def ingest_text(client_id: str, request: TextIngestRequest):
    """
    Ingest raw text safely via pipeline and append it to existing knowledge base.
    """
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    try:
        result = await ingestion_pipeline.ingest_text(
            client_id, request.text, source=request.source
        )
        response_cache.invalidate_client(client_id)
        return result.model_dump()
    except IngestionLockError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"[{client_id}] Text ingestion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# Cache Management
# ─────────────────────────────────────────────

@app.delete("/api/v1/{client_id}/cache", dependencies=[Depends(require_admin_key)])
async def clear_client_cache(client_id: str):
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    cleared = response_cache.invalidate_client(client_id)
    return {"status": "cleared", "client_id": client_id, "entries_cleared": cleared}


# ─────────────────────────────────────────────
# Global Error Handler
# ─────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


# ─────────────────────────────────────────────
# Instagram DM Integration
# ─────────────────────────────────────────────

class ManyChatRequest(BaseModel):
    message: str
    subscriber_id: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: str = ""


@app.post("/api/v1/{client_id}/instagram/chat")
async def instagram_manychat(client_id: str, request: ManyChatRequest):
    """
    ManyChat webhook endpoint.
    ManyChat sends DMs here → RAG processes → reply returned to ManyChat.
    ISOLATED: Only fires if client uses ManyChat. Zero impact on widget/email.
    """
    if not client_manager.client_exists(client_id):
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")

    from instagram_handler import ManyChatWebhookRequest
    payload = ManyChatWebhookRequest(
        message=request.message,
        subscriber_id=request.subscriber_id,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )
    result = await instagram_handler_instance.handle_manychat(client_id, payload)
    return result


@app.post("/webhook/instagram")
async def instagram_meta_webhook(request_obj: Request):
    """
    Meta Graph API webhook for Instagram DMs (direct path).
    ISOLATED: Only fires if client has Meta API configured.
    """
    body = await request_obj.json()
    result = await instagram_handler_instance.handle_meta_webhook(body)
    return {"status": result or "error"}


@app.get("/webhook/instagram")
async def instagram_meta_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """
    Meta webhook verification (one-time, during app setup).
    Meta sends GET with hub.mode, hub.verify_token, hub.challenge.
    """
    # Use a verify token from settings or env
    verify_token = os.environ.get("META_VERIFY_TOKEN", "rapidrag-verify-2024")
    challenge = instagram_handler_instance.verify_meta_webhook(
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge,
        verify_token=verify_token,
    )
    if challenge:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ─────────────────────────────────────────────
# Static Files — MUST BE LAST
# app.mount() intercepts ALL unmatched routes.
# Any @app.get / @app.post defined AFTER this line is DEAD.
# ─────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=os.path.join(settings.project_root, "static")), name="static")


# ─────────────────────────────────────────────
# Run with: python main.py  OR  uvicorn main:app --reload --host 0.0.0.0 --port 8000
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
import asyncio
import logging
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Dict, Any, Optional

from config import ClientConfig, RAGResponse
from lead_dispatcher import LeadEvent

logger = logging.getLogger(__name__)

class SessionManager:
    """
    Manages active chat sessions in-memory.
    Runs a background task to detect inactive sessions (8 minutes) and dispatch them as leads.
    """
    def __init__(self, lead_dispatcher, client_manager):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.lead_dispatcher = lead_dispatcher
        self.client_manager = client_manager
        self.inactivity_timeout = 360  # 6 minutes before session is considered inactive
        self.max_session_duration = 2 * 60 * 60  # 2 hours max
        self.task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        """Start the background monitoring task."""
        if not self._running:
            self._running = True
            self.task = asyncio.create_task(self._monitor_loop())
            logger.info("SessionManager started background monitoring task")

    async def stop(self):
        """Stop the background monitoring task."""
        if self._running:
            self._running = False
            if self.task:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            logger.info("SessionManager stopped")

    def get(self, session_id: str) -> Dict[str, Any]:
        """Get the current state of a session."""
        return self.sessions.get(session_id, {})

    def update(self, session_id: str, client_id: str, query: str, response: "RAGResponse", config: ClientConfig):
        """Update session state with a new message exchange."""
        if not session_id:
            return

        now = datetime.now(timezone.utc).timestamp()

        if session_id not in self.sessions:
            # Auto-detect source channel from session_id prefix
            source = "instagram_dm" if session_id.startswith("ig_") else "web_widget"
            self.sessions[session_id] = {
                "client_id": client_id,
                "business_name": config.business_name,
                "n8n_webhook_url": config.n8n_webhook_url,
                "owner_email": config.owner_email,
                "source_channel": source,
                "messages": [],
                "user_name": "",
                "user_phone": "",
                "user_email": "",
                "requirement": "",
                "budget": "",
                "intent": "unknown",
                "confidence": 0.0,
                "last_active": now,
                "created_at": now,
                "lead_fired": False
            }

        session = self.sessions[session_id]
        
        # Append message history
        # We store just the query and reply preview for simplicity.
        session["messages"].append({"role": "user", "content": query, "timestamp": now})
        session["messages"].append({"role": "assistant", "content": response.reply, "timestamp": now})
        
        # Cap messages to prevent unbounded growth
        if len(session["messages"]) > 40:
            session["messages"] = session["messages"][-40:]

        if hasattr(response, "user_name") and response.user_name:
            session["user_name"] = response.user_name
        if hasattr(response, "user_phone") and response.user_phone:
            session["user_phone"] = response.user_phone
        if hasattr(response, "user_email") and response.user_email:
            session["user_email"] = response.user_email
        if hasattr(response, "requirement") and response.requirement:
            session["requirement"] = response.requirement
        if hasattr(response, "budget") and response.budget:
            session["budget"] = response.budget
        
        # We update intent if the current one is stronger or if we don't have one yet.
        # For Phase 2 we simply take the latest intent/confidence if it's considered a lead intent, or just take the max confidence.
        if response.lead or response.confidence > session["confidence"]:
            session["intent"] = response.intent
            session["confidence"] = response.confidence

        session["last_active"] = now

    async def _monitor_loop(self):
        """Monitor sessions every 60 seconds for inactivity."""
        while self._running:
            try:
                await self._check_sessions()
            except Exception as e:
                logger.error(f"Error in SessionManager monitor loop: {e}", exc_info=True)
            await asyncio.sleep(5)  # Temporary testing: check every 5 seconds

    async def _check_sessions(self):
        now = datetime.now(timezone.utc).timestamp()
        expired_sessions = []

        for session_id, session in self.sessions.items():
            if session["lead_fired"]:
                continue
            
            # Fire lead if inactive for 8 minutes, or if max duration exceeded
            if (now - session["last_active"]) >= self.inactivity_timeout or (now - session.get("created_at", now)) >= self.max_session_duration:
                
                # ─── QUALITY GATE: Only dispatch if session is a real lead ───
                if self._is_quality_lead(session):
                    await self._dispatch_lead(session_id, session)
                else:
                    logger.info(f"[{session['client_id']}] Session {session_id} skipped — not a quality lead (intent={session['intent']}, msgs={len(session['messages'])})")
                
                session["lead_fired"] = True
                expired_sessions.append(session_id)

        # Cleanup memory
        for sid in expired_sessions:
            del self.sessions[sid]

    def _is_quality_lead(self, session: Dict[str, Any]) -> bool:
        """
        Smart quality gate: determines if a session should be dispatched as a lead.
        Returns False for garbage, spam, or low-value conversations.
        """
        from config import LEAD_INTENTS

        # Rule 1: Must have at least 1 user message
        user_messages = [m for m in session["messages"] if m.get("role") == "user"]
        if len(user_messages) < 1:
            return False

        # Rule 2: Must have a real lead intent
        has_lead_intent = session["intent"] in LEAD_INTENTS
        if not has_lead_intent:
            return False

        # Rule 3: Must ALWAYS have a phone number to be considered a lead.
        if not session["user_phone"]:
            return False

        # Rule 3: Check for gibberish / spam (all messages very short or all identical)
        user_texts = [m.get("content", "") for m in user_messages]
        avg_length = sum(len(t) for t in user_texts) / len(user_texts) if user_texts else 0
        if avg_length < 5:  # Average message under 5 chars = likely spam ("hi", "ok", "k")
            return False

        # Rule 4: Check for duplicate/bot spam (same message repeated)
        unique_messages = set(t.lower().strip() for t in user_texts)
        if len(unique_messages) == 1 and len(user_texts) > 2:
            return False  # Same message spammed 3+ times

        return True


    async def _dispatch_lead(self, session_id: str, session: Dict[str, Any]):
        """Assemble the complete lead and dispatch it."""
        try:
            # We skip generating LLM summary here for now (Phase 4 will do summary), 
            # just pass everything we have.
            
            # The preview is just the beginning of the first few messages or the last bot reply.
            preview = ""
            if session["messages"]:
                preview = session["messages"][-1]["content"][:200]

            event = LeadEvent(
                client_id=session["client_id"],
                business_name=session["business_name"],
                query="Full Conversation",
                reply_preview=preview,
                intent=session["intent"],
                confidence=session["confidence"],
                source_channel=session.get("source_channel", "web_widget"),
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_name=session["user_name"],
                user_phone=session["user_phone"],
                user_email=session["user_email"],
                requirement=session["requirement"],
                budget=session["budget"],
                messages=session["messages"],
                summary="",
                owner_email=session.get("owner_email", ""),
            )
            
            # We call the lead_dispatcher
            await self.lead_dispatcher.dispatch(event, session["n8n_webhook_url"])
            logger.info(f"[{session['client_id']}] Dispatched completed session {session_id} as lead")
            
        except Exception as e:
            logger.error(f"Failed to dispatch session {session_id}: {e}", exc_info=True)

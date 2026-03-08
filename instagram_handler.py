"""
RapidRAG — instagram_handler.py
Handles Instagram DM integration via two paths:
  - Path A: ManyChat webhook (client has ManyChat Pro)
  - Path B: Meta Graph API (direct, free)

SAFETY: This module is completely isolated. If Instagram is not configured
for a client, nothing happens. Widget + email continue working independently.
"""

import os
import logging
import httpx
from typing import Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────

class ManyChatWebhookRequest(BaseModel):
    """Payload from ManyChat External Request action."""
    message: str
    subscriber_id: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    # ManyChat sends these automatically


class ManyChatResponse(BaseModel):
    """Response back to ManyChat (it relays this to Instagram DM)."""
    version: str = "v2"
    content: dict


class InstagramHandler:
    """
    Handles Instagram DM processing for both ManyChat and Meta API paths.
    
    ISOLATION GUARANTEE:
    - This class only activates when explicitly called by instagram endpoints
    - If a client has no Instagram config, endpoints return early
    - Zero impact on widget chat, n8n emails, or any other feature
    """

    def __init__(self, rag_engine, session_manager, client_manager):
        self.rag_engine = rag_engine
        self.session_manager = session_manager
        self.client_manager = client_manager
        self.http_client = httpx.AsyncClient(timeout=10.0)

    # ─────────────────────────────────────────
    # Path A: ManyChat Webhook
    # ─────────────────────────────────────────

    async def handle_manychat(self, client_id: str, payload: ManyChatWebhookRequest) -> dict:
        """
        Process a DM received via ManyChat.
        ManyChat sends the user's message → we process via RAG → return reply.
        ManyChat then sends the reply back to Instagram.
        """
        try:
            config = self.client_manager.load_client(client_id)
            if not getattr(config, "is_active", True):
                return self._manychat_reply("This virtual assistant is currently unavailable.")
        except FileNotFoundError:
            logger.warning(f"ManyChat webhook for unknown client: {client_id}")
            return self._manychat_reply("Sorry, this bot is not configured yet.")

        # Build a session ID from ManyChat subscriber ID (persistent per user)
        session_id = f"ig_{client_id}_{payload.subscriber_id}" if payload.subscriber_id else f"ig_{client_id}_anon"

        # Process through the same RAG pipeline as the widget
        try:
            response = await self.rag_engine.answer(
                client_id=client_id,
                query=payload.message,
                session_id=session_id,
            )

            # Update session with Instagram-specific info
            if self.session_manager:
                session = self.session_manager.get(session_id)
                if session:
                    # Set name from ManyChat if we don't have it yet
                    name = f"{payload.first_name} {payload.last_name}".strip()
                    if name and not session.get("user_name"):
                        session["user_name"] = name
                    if payload.phone and not session.get("user_phone"):
                        session["user_phone"] = payload.phone

                self.session_manager.update(
                    session_id=session_id,
                    client_id=client_id,
                    query=payload.message,
                    response=response,
                    config=config,
                )

            logger.info(f"[{client_id}] Instagram DM via ManyChat processed: {payload.message[:50]}...")
            return self._manychat_reply(response.reply)

        except Exception as e:
            logger.error(f"[{client_id}] ManyChat handler error: {e}", exc_info=True)
            return self._manychat_reply("I'm having trouble right now. Please try again in a moment.")

    def _manychat_reply(self, text: str) -> dict:
        """Format reply for ManyChat External Request response."""
        return {
            "version": "v2",
            "content": {
                "messages": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        }

    # ─────────────────────────────────────────
    # Path B: Meta Graph API (Direct)
    # ─────────────────────────────────────────

    async def handle_meta_webhook(self, body: dict) -> Optional[str]:
        """
        Process Instagram DM received via Meta Webhook.
        Meta sends the message → we process → reply via Graph API.
        """
        try:
            # Parse Meta webhook payload
            entry = body.get("entry", [])
            if not entry:
                return None

            for item in entry:
                messaging = item.get("messaging", [])
                for msg_event in messaging:
                    sender_id = msg_event.get("sender", {}).get("id", "")
                    recipient_id = msg_event.get("recipient", {}).get("id", "")
                    message = msg_event.get("message", {})
                    text = message.get("text", "")

                    if not text or not sender_id:
                        continue

                    # Don't reply to our own messages (echo)
                    if sender_id == recipient_id:
                        continue

                    # Find which client this Instagram page belongs to
                    client_id = self._find_client_by_page_id(recipient_id)
                    if not client_id:
                        logger.warning(f"Instagram DM received for unknown page: {recipient_id}")
                        continue

                    config = self.client_manager.load_client(client_id)

                    if not getattr(config, "is_active", True):
                        await self._send_meta_reply(
                            page_access_token=config.instagram_access_token,
                            recipient_id=sender_id,
                            text="This virtual assistant is currently unavailable.",
                        )
                        continue

                    # Process through RAG
                    session_id = f"ig_{client_id}_{sender_id}"
                    response = await self.rag_engine.answer(
                        client_id=client_id,
                        query=text,
                        session_id=session_id,
                    )

                    # Update session manager
                    if self.session_manager:
                        self.session_manager.update(
                            session_id=session_id,
                            client_id=client_id,
                            query=text,
                            response=response,
                            config=config,
                        )

                    # Reply via Meta Graph API
                    await self._send_meta_reply(
                        page_access_token=config.instagram_access_token,
                        recipient_id=sender_id,
                        text=response.reply,
                    )

                    logger.info(f"[{client_id}] Instagram DM via Meta API processed: {text[:50]}...")

            return "ok"

        except Exception as e:
            logger.error(f"Meta webhook handler error: {e}", exc_info=True)
            return None

    async def _send_meta_reply(self, page_access_token: str, recipient_id: str, text: str):
        """Send a reply back to Instagram DM via Meta Graph API."""
        if not page_access_token:
            logger.warning("No Instagram access token — cannot reply via Meta API")
            return

        url = f"https://graph.instagram.com/v21.0/me/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
        headers = {
            "Authorization": f"Bearer {page_access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = await self.http_client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.debug(f"Meta API reply sent to {recipient_id}")
        except Exception as e:
            logger.error(f"Failed to send Meta API reply: {e}")

    def _find_client_by_page_id(self, page_id: str) -> Optional[str]:
        """Look up which client owns this Instagram page ID."""
        for client_id in self.client_manager.list_clients():
            try:
                config = self.client_manager.load_client(client_id)
                if config.instagram_page_id == page_id:
                    return client_id
            except Exception:
                continue
        return None

    # ─────────────────────────────────────────
    # Meta Webhook Verification
    # ─────────────────────────────────────────

    def verify_meta_webhook(self, mode: str, token: str, challenge: str, verify_token: str) -> Optional[str]:
        """
        Handle Meta's webhook verification (GET request).
        Meta sends: hub.mode=subscribe, hub.verify_token=YOUR_TOKEN, hub.challenge=CHALLENGE
        You return the challenge if token matches.
        """
        if mode == "subscribe" and token == verify_token:
            logger.info("Meta webhook verified successfully")
            return challenge
        logger.warning(f"Meta webhook verification failed: mode={mode}")
        return None

    async def close(self):
        await self.http_client.aclose()

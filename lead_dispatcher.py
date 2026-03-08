import os
import asyncio
import logging
import json
import httpx
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from config import settings

logger = logging.getLogger(__name__)


class LeadEvent(BaseModel):
    client_id: str
    business_name: str
    query: str
    reply_preview: str
    intent: str
    confidence: float
    source_channel: str = "web_widget"
    type: str = "realtime_lead"
    timestamp: str
    user_name: str = ""
    user_phone: str = ""
    user_email: str = ""
    requirement: str = ""
    budget: str = ""
    messages: list = []
    summary: str = ""
    owner_email: str = ""


class LeadDispatcher:
    """
    Handles asynchronous dispatching of lead webhooks to n8n.
    
    Improvements:
      - AI-generated conversation summary via LLM
      - Clean, business-friendly payload formatting
      - Retry logic (3 attempts with backoff)
      - Human-readable conversation transcript
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self._background_tasks = set()
        # Will be set by main.py after initialization
        self._llm_summarizer = None

    def set_llm_summarizer(self, rag_engine):
        """Allow main.py to inject the RAG engine for LLM summary calls."""
        self._llm_summarizer = rag_engine

    async def dispatch(self, event: LeadEvent, webhook_url: str):
        # Always save locally for reporting, even if no webhook
        self._save_lead_to_file(event)

        if not webhook_url:
            logger.warning(f"[{event.client_id}] No webhook URL configured for lead dispatch.")
            return

        # ─── Safety check: don't send if no owner email configured ───
        if not event.owner_email:
            logger.warning(f"[{event.client_id}] No owner_email configured — lead saved locally but not emailed.")
            return

        try:
            # ─── Validate & clean phone number ───
            event.user_phone = self._validate_phone(event.user_phone)

            # ─── Validate & clean all fields (no blank garbage) ───
            event.user_name = self._clean_field(event.user_name)
            event.requirement = self._clean_field(event.requirement)
            event.budget = self._clean_field(event.budget)

            # Generate AI summary if we have an LLM connection
            if self._llm_summarizer and event.messages:
                try:
                    event.summary = await self._generate_summary(event)
                except Exception as e:
                    logger.warning(f"[{event.client_id}] Summary generation failed, using fallback: {e}")
                    event.summary = self._fallback_summary(event)
            elif not event.summary or event.summary.startswith("Summary generation"):
                event.summary = self._fallback_summary(event)

            # Build clean, business-friendly payload
            payload = self._build_clean_payload(event)
            logger.info(f"[{event.client_id}] Dispatching lead event to {webhook_url}")

            # Fire-and-forget with retry
            task = asyncio.create_task(self._send_with_retry(webhook_url, payload, event.client_id))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except Exception as e:
            logger.error(f"Failed to dispatch lead event: {e}")

    # ─────────────────────────────────────────
    # Field Validation & Cleanup
    # ─────────────────────────────────────────

    def _validate_phone(self, phone: str) -> str:
        """Validate phone number format. Returns cleaned phone or empty string."""
        import re
        if not phone:
            return ""
        
        # Strip everything except digits and leading +
        cleaned = re.sub(r'[^\d+]', '', phone.strip())
        
        # Must have at least 7 digits (shortest valid phone numbers)
        digit_count = len(re.sub(r'\D', '', cleaned))
        if digit_count < 7 or digit_count > 15:
            logger.info(f"Phone number rejected (invalid length): {phone}")
            return ""
        
        # Check for obvious fakes: all same digit, sequential
        digits_only = re.sub(r'\D', '', cleaned)
        if len(set(digits_only)) <= 2:  # e.g. "1111111111" or "1212121212"
            logger.info(f"Phone number rejected (likely fake): {phone}")
            return ""
        
        return cleaned

    def _clean_field(self, value: str) -> str:
        """Clean a text field: strip whitespace, remove garbage characters."""
        if not value:
            return ""
        cleaned = value.strip()
        # Reject if it's just punctuation, numbers under 2 chars, or obvious nonsense
        if len(cleaned) < 2:
            return ""
        # Reject if it's all the same character repeated
        if len(set(cleaned.lower())) <= 1:
            return ""
        return cleaned

    # ─────────────────────────────────────────
    # AI Summary Generation
    # ─────────────────────────────────────────

    async def _generate_summary(self, event: LeadEvent) -> str:
        """Use the LLM to generate a short, professional conversation summary."""
        from config import settings, LLM_PROVIDERS

        # Build a compact transcript for the LLM
        transcript_lines = []
        for msg in event.messages[-20:]:  # Last 20 messages max
            role_label = "Customer" if msg.get("role") == "user" else "Bot"
            transcript_lines.append(f"{role_label}: {msg.get('content', '')}")
        transcript = "\n".join(transcript_lines)

        summary_prompt = f"""You are a senior sales analyst reviewing a chat log. Create a highly professional, actionable "Executive Summary" for the business owner.

Business: {event.business_name}
Customer Name: {event.user_name or 'Not provided'}
Customer Phone: {event.user_phone or 'Not provided'}
Customer Email: {event.user_email or 'Not provided'}
Detected Intent: {event.intent}

--- CONVERSATION ---
{transcript}
--- END ---

Write a strict 3-bullet summary highlighting ONLY:
1. The Customer's Core Motivation/Need
2. Key Details (Budget, Preferences, Timeline)
3. Recommended Next Action for the Sales Rep

Format as 3 clean sentences. Do not use markdown headers, just text."""

        # Use the fallback list of LLM models
        models_to_try = getattr(settings, "fallback_llm_models", [settings.default_llm_model])
        
        for current_model_string in models_to_try:
            try:
                provider, model = current_model_string.split("/", 1)
                provider_config = LLM_PROVIDERS.get(provider)

                if not provider_config:
                    continue

                key_env = provider_config.get("key_env")
                api_key = None
                if key_env:
                    settings_attr = key_env.lower()
                    api_key = getattr(settings, settings_attr, None) or os.getenv(key_env)

                if key_env and not api_key:
                    continue

                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a concise business lead summarizer. Output plain text only."},
                        {"role": "user", "content": summary_prompt},
                    ],
                    "max_tokens": 150,
                    "temperature": 0.3, # Keep summary temperature low for factual accuracy
                }

                response = await self.client.post(
                    f"{provider_config['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                summary = data["choices"][0]["message"]["content"].strip()
                logger.info(f"[{event.client_id}] Generated AI summary for lead using {current_model_string}")
                return summary

            except Exception as e:
                logger.warning(f"[{event.client_id}] Summary generation failed with {current_model_string}: {e}, falling back...")
                continue
                
        # If all models failed, use fallback summary
        return self._fallback_summary(event)

    def _fallback_summary(self, event: LeadEvent) -> str:
        """Generate a simple rule-based summary when LLM is unavailable."""
        parts = []
        if event.user_name:
            parts.append(f"{event.user_name} chatted")
        else:
            parts.append("A visitor chatted")

        parts.append(f"with the {event.business_name} bot")

        if event.intent and event.intent != "unknown":
            intent_labels = {
                "pricing_query": "asking about pricing",
                "service_inquiry": "inquiring about services",
                "appointment_request": "wanting to book an appointment",
                "contact_request": "requesting contact information",
                "complaint": "raising a complaint",
                "faq": "asking general questions",
                "general": "with general questions",
            }
            parts.append(intent_labels.get(event.intent, f"with intent: {event.intent}"))

        if event.requirement:
            parts.append(f"— interested in: {event.requirement}")
        if event.budget:
            parts.append(f"(budget: {event.budget})")

        msg_count = len(event.messages)
        parts.append(f"over {msg_count // 2} exchanges.")

        return " ".join(parts)

    # ─────────────────────────────────────────
    # Clean Business-Friendly Payload
    # ─────────────────────────────────────────

    def _build_clean_payload(self, event: LeadEvent) -> dict:
        """
        Build a clean, well-organized payload that n8n can easily
        use in email templates. Business owners should understand
        every field at a glance.
        """
        # Format the conversation into a human-readable transcript
        transcript_lines = []
        for msg in event.messages:
            role_label = "🧑 Customer" if msg.get("role") == "user" else "🤖 Bot"
            content = msg.get("content", "")
            transcript_lines.append(f"{role_label}: {content}")
        transcript = "\n\n".join(transcript_lines)

        # Format timestamp to human-readable
        try:
            dt = datetime.fromisoformat(event.timestamp)
            readable_time = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except Exception:
            readable_time = event.timestamp

        # Build the confidence label
        if event.confidence >= 0.7:
            lead_quality = "🔥 Hot Lead"
        elif event.confidence >= 0.4:
            lead_quality = "🟡 Warm Lead"
        else:
            lead_quality = "🔵 Mild Interest"

        # Build clean intent label
        intent_labels = {
            "pricing_query": "💰 Pricing Inquiry",
            "service_inquiry": "🔍 Service Inquiry",
            "appointment_request": "📅 Appointment Request",
            "contact_request": "📞 Contact Request",
            "complaint": "⚠️ Complaint",
            "faq": "❓ FAQ",
            "general": "💬 General Chat",
            "unknown": "💬 General Chat",
        }

        return {
            # ── Lead Essentials (top-level for easy n8n mapping) ──
            "lead_quality": lead_quality,
            "summary": event.summary,
            "business_name": event.business_name,
            "client_id": event.client_id,
            "owner_email": event.owner_email,

            # ── Customer Info ──
            "customer": {
                "name": event.user_name or "Not provided",
                "phone": event.user_phone or "Not provided",
                "email": event.user_email or "Not provided",
                "requirement": event.requirement or "Not specified",
                "budget": event.budget or "Not specified",
            },

            # ── Conversation Details ──
            "conversation": {
                "intent": intent_labels.get(event.intent, event.intent),
                "confidence_score": round(event.confidence, 2),
                "total_messages": len(event.messages),
                "transcript": transcript,
                "reply_preview": event.reply_preview,
            },

            # ── Metadata ──
            "metadata": {
                "timestamp": readable_time,
                "timestamp_iso": event.timestamp,
                "source_channel": event.source_channel,
                "type": event.type,
            },

            # ── Raw data (if n8n needs programmatic access) ──
            "raw_messages": event.messages,
        }

    # ─────────────────────────────────────────
    # Webhook Delivery with Retry
    # ─────────────────────────────────────────

    async def _send_with_retry(self, url: str, payload: dict, client_id: str, max_retries: int = 3):
        """Send webhook payload with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                logger.info(f"[{client_id}] ✓ Lead dispatched to n8n (attempt {attempt + 1})")
                return
            except httpx.TimeoutException:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"[{client_id}] Webhook timeout (attempt {attempt + 1}/{max_retries}), retrying in {wait}s...")
                await asyncio.sleep(wait)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"[{client_id}] Webhook {e.response.status_code} (attempt {attempt + 1}/{max_retries}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"[{client_id}] Webhook failed permanently: {e.response.status_code} — {e.response.text[:200]}")
                    self._save_failed_webhook(client_id, payload, f"HTTP Error {e.response.status_code}")
                    return
            except Exception as e:
                logger.error(f"[{client_id}] Webhook error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self._save_failed_webhook(client_id, payload, f"Webhook error: {e}")
                    return

        logger.error(f"[{client_id}] ✗ Lead dispatch failed after {max_retries} attempts to {url}")
        self._save_failed_webhook(client_id, payload, "Max retries exceeded")

    # ─────────────────────────────────────────
    # Local File Storage
    # ─────────────────────────────────────────

    def _save_failed_webhook(self, client_id: str, payload: dict, reason: str):
        """Save permanently failed webhooks to a separate file for review."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        client_dir = os.path.join(settings.log_dir, client_id, "leads")
        os.makedirs(client_dir, exist_ok=True)
        filepath = os.path.join(client_dir, f"failed_webhooks_{today}.jsonl")

        failed_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "payload": payload
        }
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(failed_entry) + "\n")
        except Exception as e:
            logger.error(f"[{client_id}] Failed to save failed webhook data to file: {e}")

    def _save_lead_to_file(self, event: LeadEvent):
        """Append the lead payload to the local batch file for report_engine to read."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        client_dir = os.path.join(settings.log_dir, event.client_id, "leads")
        os.makedirs(client_dir, exist_ok=True)
        filepath = os.path.join(client_dir, f"{today}.jsonl")

        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.model_dump()) + "\n")
        except Exception as e:
            logger.error(f"[{event.client_id}] Failed to save lead to file: {e}")

    async def close(self):
        await self.client.aclose()

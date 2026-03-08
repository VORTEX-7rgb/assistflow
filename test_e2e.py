import sys
import time
import json
import urllib.request
import urllib.parse
from urllib.error import URLError

def q(url, data=None):
    if data:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req) as r:
            res = r.read().decode()
            try: return json.loads(res)
            except: return {"raw": res}
    except Exception as e:
        status = getattr(e, "code", 500)
        body = None
        if hasattr(e, 'read'):
            try:
                body = e.read().decode()
            except:
                pass
        return {"error": str(e), "body": body, "status": status}

client_id = "test_e2e_run"
base_url = "http://127.0.0.1:8000"

print("\n--- 1. Healthcheck ---")
result = q(f"{base_url}/health")
print(result)
if result.get("status") != "ok":
    print("Healthcheck failed!", file=sys.stderr)
    sys.exit(1)

print("\n--- 2. Create Client ---")
client_data = {
    "client_id": client_id,
    "business_name": "RapidRAG Auto Testing",
    "business_type": "Technology Provider",
    "website_url": "https://example.com",
    "contact_email": "test@example.com",
    "contact_phone": "+1-800-555-0199",
    "bot_name": "TestBot"
}
result = q(f"{base_url}/api/v1/clients", client_data)
if result.get('status') == 409:
    print("Test client already exists. Continuing.")
else:
    print(result)

print("\n--- 3. Ingest Text ---")
ingest_data = {
    "text": "RapidRAG offers the best conversational AI chat agents. Pricing starts at $99 per month for a single agent. To book a demo, reply with your contact information.",
    "source": "manual-testing-source"
}
result = q(f"{base_url}/api/v1/{client_id}/ingest/text", ingest_data)
print(result)

print("\n--- 4. Chat 1 (General Question) ---")
chat_data1 = {
    "query": "What do you sell and how much is it?",
    "session_id": "test_session_123"
}
result = q(f"{base_url}/api/v1/{client_id}/chat", chat_data1)
print(result)

print("\n--- 5. Chat 2 (Lead Intent) ---")
chat_data2 = {
    "query": "That sounds great, I'd like to book a demo. My name is Alice.",
    "session_id": "test_session_123"
}
result = q(f"{base_url}/api/v1/{client_id}/chat", chat_data2)
print(result)

print("\n--- 6. Test Widget Realtime Lead Capture ---")
chat_data3 = {
    "query": "[SYSTEM: USER SUBMITTED CONTACT FORM] Name: Alice Jones, Phone: 555-123-4567. Acknowledge receipt.",
    "session_id": "test_session_123"
}
result = q(f"{base_url}/api/v1/{client_id}/chat", chat_data3)
print(result)

print("\n--- 7. Cleanup ---")
import shutil
import os
try:
    shutil.rmtree(os.path.join("clients", client_id))
    print(f"Cleaned up {client_id} database.")
except Exception as e:
    print(f"Cleanup error: {e}")

print("\n--- Done ---")

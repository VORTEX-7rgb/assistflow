import urllib.request
import urllib.parse
import json

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
        err = getattr(e, "read", lambda: lambda:"")()() 
        return {"error": str(e), "body": err.decode() if err else None, "status": status}
client = "test_test"
print("1. INGEST WEBSITE")
print(q(f"http://127.0.0.1:8000/api/v1/{client}/ingest/website", {"url": "https://example.com"}))

import shutil
import os

print("\n2. TEARDOWN")
try:
    client_dir = os.path.join("clients", client)
    if os.path.exists(client_dir):
        shutil.rmtree(client_dir)
        print(f"Cleaned up {client_dir} directory")
except Exception as e:
    print(f"Teardown error: {e}")

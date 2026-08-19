# filename: net-diag.py
import socket
import httpx
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def test_dns():
    try:
        ip = socket.gethostbyname("api.openai.com")
        print(f"[DNS] api.openai.com -> {ip}")
    except Exception as e:
        print(f"[DNS] ERROR: {e}")

def test_port_open(host="127.0.0.1", port=8000):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(2)
        s.connect((host, port))
        print(f"[PORT] {host}:{port} OPEN")
    except Exception as e:
        print(f"[PORT] {host}:{port} CLOSED or blocked ({e})")
    finally:
        s.close()

async def test_openai():
    if not OPENAI_API_KEY:
        print("[OPENAI] API Key not set")
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.gapgpt.app/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4.1",
                    "messages":[{"role":"user","content":"سلام"}],
                    "max_tokens": 10
                },
            )
            print(f"[OPENAI] Status: {resp.status_code}")
            print(f"[OPENAI] Response: {resp.text}")
    except Exception as e:
        print(f"[OPENAI] Connection ERROR: {e}")

if __name__ == "__main__":
    import asyncio
    print("=== INOTEX Assistant Network Diagnostic ===\n")

    # 1. DNS
    test_dns()

    # 2. Check if local ports are open (uvicorn ports)
    test_port_open(8000)
    test_port_open(8001)

    # 3. Test OpenAI API
    asyncio.run(test_openai())

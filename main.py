import os

import uvicorn


if __name__ == "__main__":
    # Host/port are overridable via env so the dev server can dodge a busy port
    # (e.g. another local service already on 8000) without editing code.
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=True)

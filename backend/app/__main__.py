"""`python3 -m app` starts the API with host/port from config.toml."""

import uvicorn

from .core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, proxy_headers=True)

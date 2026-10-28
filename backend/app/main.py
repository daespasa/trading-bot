import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import settings
from app.core.logging import setup_logging
from app.database import session as db
from app.services.engine import engine

setup_logging()
log = logging.getLogger(__name__)
clients: set[WebSocket] = set()


async def broadcaster():
    """Batched push: at most one message every 2 s, telling the UI which query groups changed."""
    while True:
        await asyncio.sleep(2)
        if not clients:
            engine.dirty.clear()
            continue
        msg = json.dumps({"type": "tick", "ts": time.time(), "changed": sorted(engine.dirty),
                          "health": {k: v for k, v in engine.health().items() if k != "loop_errors"},
                          "notifications": list(engine.notifications)[:3]})
        engine.dirty.clear()
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_engine()
    await engine.start()
    task = asyncio.create_task(broadcaster())
    yield
    task.cancel()
    await engine.stop()
    await db.engine().dispose()


app = FastAPI(title="Polymarket Paper Trading Lab", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"],
                   allow_headers=["*"])


def _authorized(token: str | None) -> bool:
    return not settings.api_token or token == settings.api_token


CSRF_HEADER = "x-requested-with"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def auth(request: Request, call_next):
    # CSRF: state-changing calls need a custom header. A foreign site cannot add it without a CORS preflight,
    # and CORS only admits the dashboard's origins.
    if request.url.path.startswith("/api") and request.method not in SAFE_METHODS \
            and request.headers.get(CSRF_HEADER) != "paper-lab":
        return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
    if request.url.path.startswith("/api") and request.url.path != "/api/health" and request.method != "OPTIONS":
        tok = request.headers.get("authorization", "").removeprefix("Bearer ") or request.query_params.get("token")
        if not _authorized(tok):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(router)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not _authorized(ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # client pings; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)

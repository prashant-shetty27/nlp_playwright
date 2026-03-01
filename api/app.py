"""
api/app.py
FastAPI application factory.

Start with:
  uvicorn api.app:app --reload --port 8000

Routes:
  GET  /health
  POST /nlp/parse
  POST /nlp/suggest
  GET  /locators
  GET  /locators/{page}
  POST /locators
  DEL  /locators/{page}/{name}
  GET  /projects
  POST /projects
  GET  /projects/{name}
  PUT  /projects/{name}
  DEL  /projects/{name}
  POST /tests/run
  GET  /tests/results
  GET  /tests/results/{run_id}
  WS   /ws/test-stream
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health, nlp, locators, projects, tests, websocket

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("🚀 NLP-Playwright API starting up...")
    # Pre-warm the action registry so first /tests/run isn't slow
    import execution.action_service  # noqa: F401 — registers @codeless_snippet
    yield
    logger.info("🛑 NLP-Playwright API shutting down.")


app = FastAPI(
    title="NLP-Playwright API",
    description=(
        "REST + WebSocket API for the NLP-driven Playwright automation framework.\n\n"
        "- **NLP**: parse natural language steps, get autocomplete suggestions\n"
        "- **Locators**: CRUD for the element DNA database\n"
        "- **Projects**: manage `.flow` test script files\n"
        "- **Tests**: run flows, fetch results\n"
        "- **WebSocket `/ws/test-stream`**: live step-by-step execution stream\n"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten this when you add a frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(nlp.router)
app.include_router(locators.router)
app.include_router(projects.router)
app.include_router(tests.router)
app.include_router(websocket.router)

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
  GET  /locators/dropdown/names
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

from api.routes import (assist, generate, health, locators, nlp, projects,
                        review, sources, stepgroups, system, testdata,
                        tests, websocket)

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
    # Scheduled Test Plans fire from inside this process (it owns the browser).
    try:
        from execution import scheduler
        scheduler.start()
    except Exception:  # noqa: BLE001 — never block startup on the scheduler
        logger.exception("Scheduler did not start")
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
app.include_router(system.router)
app.include_router(nlp.router)
app.include_router(locators.router)
app.include_router(projects.router)
app.include_router(tests.router)
app.include_router(sources.router)
app.include_router(generate.router)
app.include_router(testdata.router)
app.include_router(stepgroups.router)
app.include_router(review.router)
app.include_router(assist.router)
app.include_router(websocket.router)
from api.routes import users as _users_routes  # noqa: E402
app.include_router(_users_routes.router)
from api.routes import suites_plans as _sp_routes  # noqa: E402
app.include_router(_sp_routes.router)


# ── Static files ───────────────────────────────────────────────────────────────
# Run screenshots, so a report can show the page as each step saw it.
#
# Mounted on the FastAPI app rather than through NiceGUI: the UI is mounted on
# this same app, so one mount serves the report page, the API and anything that
# exports a report later. Reports store paths RELATIVE to this directory, which
# is what makes a report still readable after the data directory moves.
def _mount_screenshots() -> None:
    import os

    from fastapi.staticfiles import StaticFiles

    from config.settings import SCREENSHOTS_DIR

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR),
              name="screenshots")


_mount_screenshots()

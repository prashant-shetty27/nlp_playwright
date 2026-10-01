"""
api/routes/health.py
GET /health  — liveness probe
"""
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/share-url")
def share_url():
    """The address teammates open (and that Slack / PDF links use)."""
    from config.settings import portal_base_url
    return {"url": portal_base_url()}

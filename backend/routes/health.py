"""Health-check route."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict:
    """Return liveness info and which market provider is active."""
    provider = getattr(request.app.state, "provider", None)
    provider_name = type(provider).__name__ if provider is not None else None
    return {"status": "ok", "provider": provider_name}

"""Prometheus metrics endpoint."""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.metrics import generate_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", summary="Prometheus metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        generate_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

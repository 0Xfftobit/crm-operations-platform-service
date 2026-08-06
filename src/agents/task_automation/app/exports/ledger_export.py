"""Ledger export — local filesystem with optional S3 upload."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ledger import list_ledger

logger = logging.getLogger(__name__)


def export_ledger(*, lead_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
    settings = get_settings()
    entries = list_ledger(lead_id=lead_id, limit=limit)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"ledger_export_{lead_id or 'all'}_{ts}.json"
    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "lead_id": lead_id,
        "count": len(entries),
        "entries": entries,
    }
    body = json.dumps(payload, indent=2, default=str)

    export_dir = Path(settings.export_local_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    local_path = export_dir / filename
    local_path.write_text(body, encoding="utf-8")

    result: dict[str, Any] = {
        "provider": "local",
        "path": str(local_path),
        "count": len(entries),
        "filename": filename,
    }

    if settings.s3_export_bucket:
        s3_result = _upload_s3(filename=filename, body=body)
        result.update(s3_result)
    return result


def _upload_s3(*, filename: str, body: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        import boto3
    except ImportError:
        logger.warning("boto3_not_installed")
        return {"s3_uploaded": False, "reason": "boto3_not_installed"}

    try:
        client = boto3.client("s3", region_name=settings.s3_export_region or None)
        key = f"{settings.s3_export_prefix.rstrip('/')}/{filename}"
        client.put_object(
            Bucket=settings.s3_export_bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        return {
            "s3_uploaded": True,
            "s3_bucket": settings.s3_export_bucket,
            "s3_key": key,
            "provider": "s3",
        }
    except Exception as exc:
        logger.warning("s3_upload_failed", extra={"error": str(exc)})
        return {"s3_uploaded": False, "reason": str(exc)}

"""WORM (write-once-read-many) audit mirror — object-lock tamper-evidence (design §7/8).

Each audit entry is mirrored to an immutable object keyed by ``seq``. Two backends:

- **local**: one write-once file per entry; existing files are never overwritten and are made
  read-only (a filesystem stand-in for object-lock, for dev/on-prem).
- **s3**: S3 Object Lock in COMPLIANCE mode with a retention window — not even root can delete or
  overwrite before it elapses (the production tamper-evidence guarantee). Lazily imports boto3.

Selected by ``WORM_GATEWAY``; ``none`` disables mirroring. Mirroring is best-effort defense-in-depth
— the SQL/in-memory ledger remains the source of truth, so WORM failures never block a write.
"""
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from threading import RLock

from app.core.config import Settings, get_settings
from app.domain.models import AuditEntry


def _key(seq: int, prefix: str = "") -> str:
    return f"{prefix}{seq:012d}.json"


def _payload(entry: AuditEntry) -> bytes:
    return json.dumps(entry.model_dump(mode="json"), sort_keys=True).encode()


class LocalWormStore:
    """Write-once files; a second write to the same seq is refused (object-lock semantics)."""

    def __init__(self, directory: str) -> None:
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)

    def put(self, entry: AuditEntry) -> None:
        path = os.path.join(self.dir, _key(entry.seq))
        if os.path.exists(path):
            raise FileExistsError(f"WORM violation: {path} already written")
        # O_EXCL guarantees we are the sole writer; then drop write permission.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        try:
            os.write(fd, _payload(entry))
        finally:
            os.close(fd)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def get(self, seq: int) -> dict | None:
        path = os.path.join(self.dir, _key(seq))
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return json.loads(f.read())

    def count(self) -> int:
        return sum(1 for n in os.listdir(self.dir) if n.endswith(".json"))


class S3WormStore:
    """S3 Object Lock (COMPLIANCE mode) — immutable until the retention window elapses."""

    def __init__(self, bucket: str, prefix: str, retention_days: int) -> None:
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix
        self.retention_days = retention_days

    def put(self, entry: AuditEntry) -> None:
        retain_until = datetime.now(UTC) + timedelta(days=self.retention_days)
        self.s3.put_object(
            Bucket=self.bucket, Key=_key(entry.seq, self.prefix), Body=_payload(entry),
            ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=retain_until,
            ContentType="application/json")

    def get(self, seq: int) -> dict | None:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=_key(seq, self.prefix))
            return json.loads(obj["Body"].read())
        except Exception:  # noqa: BLE001 — missing key → None
            return None


_lock = RLock()
_store = None
_store_sig: tuple | None = None


def _get_store(settings: Settings):
    """Build (and cache) the configured store; None when disabled."""
    global _store, _store_sig
    sig = (settings.worm_gateway, settings.worm_local_dir, settings.worm_s3_bucket)
    with _lock:
        if sig != _store_sig:
            _store_sig = sig
            if settings.worm_gateway == "local":
                _store = LocalWormStore(settings.worm_local_dir)
            elif settings.worm_gateway == "s3":
                _store = S3WormStore(settings.worm_s3_bucket, settings.worm_s3_prefix,
                                     settings.worm_s3_retention_days)
            else:
                _store = None
        return _store


def enabled() -> bool:
    return get_settings().worm_gateway in ("local", "s3")


def mirror(entry: AuditEntry) -> None:
    """Mirror one entry to the WORM store (no-op when disabled)."""
    store = _get_store(get_settings())
    if store is not None:
        store.put(entry)


def fetch(seq: int) -> dict | None:
    store = _get_store(get_settings())
    return store.get(seq) if store is not None else None


def reset() -> None:
    """Drop the cached store (tests switching backends)."""
    global _store, _store_sig
    with _lock:
        _store, _store_sig = None, None

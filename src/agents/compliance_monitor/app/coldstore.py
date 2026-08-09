"""Cold-storage archive for aged audit records (CEAAPSS-566 — 'cold storage for records older than
1 year'; retention default 7y).

Records past the cold-age threshold are archived here as compact per-range JSON objects so the hot
store (Postgres) stays small and cheap, while every record remains retrievable for a regulatory
inspection. Being archived here is also the precondition for *purging* an aged range from the hot
ledger — nothing is ever deleted before it is preserved in cold storage.

Backends: ``local`` (a directory of range files) or ``s3`` (a bucket/prefix, e.g. Glacier storage
class), selected by ``COLD_STORE_GATEWAY``; ``none`` disables archiving (and therefore purge).
"""
import json
import os
from threading import RLock

from app.core.config import Settings, get_settings
from app.domain.models import AuditEntry


def _key(seq_from: int, seq_to: int, prefix: str = "") -> str:
    return f"{prefix}{seq_from:012d}-{seq_to:012d}.json"


def _parse_key(name: str, prefix: str = "") -> tuple[int, int] | None:
    if not name.endswith(".json"):
        return None
    body = name[len(prefix):] if name.startswith(prefix) else name
    body = body[:-5]
    if "-" not in body:
        return None
    lo_s, hi_s = body.rsplit("-", 1)
    try:
        return int(lo_s), int(hi_s)
    except ValueError:
        return None


class LocalColdStore:
    def __init__(self, directory: str) -> None:
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)

    def archive(self, entries: list[AuditEntry]) -> str:
        key = _key(entries[0].seq, entries[-1].seq)
        path = os.path.join(self.dir, key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([e.model_dump(mode="json") for e in entries], f, sort_keys=True)
        return key

    def archived_seqs(self) -> set[int]:
        seqs: set[int] = set()
        for name in os.listdir(self.dir):
            parsed = _parse_key(name)
            if parsed:
                lo, hi = parsed
                seqs.update(range(lo, hi + 1))
        return seqs

    def fetch(self, seq: int) -> dict | None:
        for name in os.listdir(self.dir):
            parsed = _parse_key(name)
            if not parsed:
                continue
            lo, hi = parsed
            if lo <= seq <= hi:
                with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                    for rec in json.load(f):
                        if rec["seq"] == seq:
                            return rec
        return None


class S3ColdStore:
    def __init__(self, bucket: str, prefix: str) -> None:
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix

    def _range_keys(self) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.rsplit("/", 1)[-1]
                parsed = _parse_key(name, self.prefix)
                if parsed:
                    ranges.append((*parsed, key))
        return ranges

    def archive(self, entries: list[AuditEntry]) -> str:
        key = _key(entries[0].seq, entries[-1].seq, self.prefix)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            ContentType="application/json",
            Body=json.dumps([e.model_dump(mode="json") for e in entries]).encode("utf-8"),
        )
        return key

    def archived_seqs(self) -> set[int]:
        seqs: set[int] = set()
        for lo, hi, _ in self._range_keys():
            seqs.update(range(lo, hi + 1))
        return seqs

    def fetch(self, seq: int) -> dict | None:
        for lo, hi, key in self._range_keys():
            if lo <= seq <= hi:
                body = (
                    self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
                )
                for rec in json.loads(body):
                    if rec["seq"] == seq:
                        return rec
        return None


_lock = RLock()
_store = None
_sig: tuple | None = None


def _get_store(settings: Settings):
    global _store, _sig
    sig = (settings.cold_store_gateway, settings.cold_store_dir, settings.cold_store_s3_bucket)
    with _lock:
        if sig != _sig:
            _sig = sig
            if settings.cold_store_gateway == "local":
                _store = LocalColdStore(settings.cold_store_dir)
            elif settings.cold_store_gateway == "s3":
                _store = S3ColdStore(settings.cold_store_s3_bucket, settings.cold_store_s3_prefix)
            else:
                _store = None
        return _store


def enabled() -> bool:
    return get_settings().cold_store_gateway in ("local", "s3")


def archive(entries: list[AuditEntry]) -> str | None:
    store = _get_store(get_settings())
    return store.archive(entries) if store is not None and entries else None


def archived_seqs() -> set[int]:
    store = _get_store(get_settings())
    return store.archived_seqs() if store is not None else set()


def fetch(seq: int) -> dict | None:
    store = _get_store(get_settings())
    return store.fetch(seq) if store is not None else None


def reset() -> None:
    global _store, _sig
    with _lock:
        _store, _sig = None, None

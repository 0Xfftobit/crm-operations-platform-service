"""Minimal dependency-free PDF writer for audit exports (CEAAPSS-566 — 'exportable as CSV and PDF').

Renders audit entries as paginated monospaced text into a valid multi-page PDF (Helvetica). Kept
self-contained (no reportlab) so export always works in any deployment. Not a layout engine — just a
faithful, regulator-submittable rendering of the trail.
"""
from app.domain.models import AuditEntry

_LINES_PER_PAGE = 50
_FONT_SIZE = 9
_LEADING = 13


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _page_stream(lines: list[str]) -> bytes:
    body = ["BT", f"/F1 {_FONT_SIZE} Tf", f"{_LEADING} TL", "1 0 0 1 36 756 Tm"]
    for ln in lines:
        body.append(f"({_escape(ln[:110])}) Tj")
        body.append("T*")
    body.append("ET")
    return "\n".join(body).encode("latin-1", "replace")


def _paginate(entries: list[AuditEntry], title: str) -> list[list[str]]:
    rows = [title, "=" * 100, ""]
    for e in entries:
        rows.append(f"seq {e.seq} | {e.occurred_at.isoformat()} | {e.event_type} | "
                    f"{e.actor_type}:{e.actor_id or '-'} | {e.entity_type}:{e.entity_id or '-'}")
        rows.append(f"    action={e.action or '-'} outcome={e.outcome or '-'} "
                    f"engine={e.engine or '-'} hash={e.entry_hash[:16]}…")
    pages, cur = [], []
    for r in rows:
        cur.append(r)
        if len(cur) >= _LINES_PER_PAGE:
            pages.append(cur)
            cur = []
    if cur or not pages:
        pages.append(cur)
    return pages


def render(entries: list[AuditEntry], *,
           title: str = "CR Equity — Compliance Audit Trail") -> bytes:
    """Build a valid multi-page PDF document from the audit entries."""
    pages = _paginate(entries, title)
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)                      # 1-based object number

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids_ids: list[int] = []
    # Create content + page objects first, then the Pages tree, then the Catalog.
    page_obj_ids: list[int] = []
    for lines in pages:
        stream = _page_stream(lines)
        content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_placeholder = add(b"")              # filled after we know the Pages id
        page_obj_ids.append(page_placeholder)
        kids_ids.append(page_placeholder)
        # stash content id on the page for the second pass
        objects[page_placeholder - 1] = (content_id, font_id)  # type: ignore[assignment]

    pages_id = add(b"")                           # Pages tree
    kids = " ".join(f"{i} 0 R" for i in kids_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Count {len(kids_ids)} /Kids [{kids}] "
        f"/MediaBox [0 0 612 792] >>").encode("latin-1")
    for pid in page_obj_ids:
        content_id, fid = objects[pid - 1]        # type: ignore[misc]
        objects[pid - 1] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {fid} 0 R >> >> >>").encode("latin-1")
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))

    # Serialize with a correct xref table.
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (len(objects) + 1)
    for i, obj in enumerate(objects, start=1):
        offsets[i] = len(out)
        out += f"{i} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for i in range(1, len(objects) + 1):
        out += f"{offsets[i]:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("latin-1")
    return bytes(out)

"""Minimal stdlib-only PDF generator for report exports."""

from __future__ import annotations

from typing import Any


def _stream(content: bytes) -> bytes:
    return f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream\n"


def build_pdf(title: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    """Build a tiny but valid PDF with a title and a table of rows."""
    lines = ["BT", "/F1 12 Tf"]
    y = 720
    lines.append(f"72 {y} Td ({_esc(title)}) Tj")
    y -= 24
    lines.append(f"0 -24 Td ({_esc(' | '.join(headers))}) Tj")
    for row in rows[:100]:
        y -= 18
        if y < 72:
            break
        text = " | ".join(str(c) for c in row)[:200]
        lines.append(f"0 -18 Td ({_esc(text)}) Tj")
    lines.append("ET")
    content = ("\n".join(lines)).encode()

    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    pages = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    stream = _stream(content)
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    # Build PDF with valid cross-reference offsets.
    return _rebuild_pdf(catalog, pages, page, stream, font)


def _rebuild_pdf(catalog: bytes, pages: bytes, page: bytes, stream: bytes, font: bytes) -> bytes:
    objects = [catalog, pages, page, stream, font]
    offsets: list[int] = []
    parts: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(b"".join(parts)))
        parts.append(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")

    body = b"".join(parts)
    xref_offset = len(body)
    xref = b"xref\n"
    xref += f"0 {len(objects) + 1}\n".encode()
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode()
    return body + xref + trailer


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_csv(headers: list[str], rows: list[list[Any]]) -> bytes:
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return out.getvalue().encode("utf-8")

"""Aggregate each upstream agent's OpenAPI schema into one gateway-facing spec.

Paths are prefixed with /{slug} so Swagger UI at /docs shows every agent endpoint as
callable through the gateway proxy.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

import httpx


def _rewrite_refs(node: Any, rename: Mapping[str, str]) -> Any:
    """Rewrite #/components/schemas/Foo refs after schema namespacing."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/"):
                old = v.rsplit("/", 1)[-1]
                out[k] = f"#/components/schemas/{rename.get(old, old)}"
            else:
                out[k] = _rewrite_refs(v, rename)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(v, rename) for v in node]
    return node


async def build_aggregated_openapi(
    *,
    title: str,
    summary: str,
    gateway_schema: dict[str, Any],
    agents: Mapping[str, str],
    upstream_fn: Callable[[str], str],
    timeout: float = 5.0,
) -> dict[str, Any]:
    info = dict(gateway_schema.get("info") or {})
    info["title"] = title
    if summary:
        info["description"] = summary

    # Keep only concrete gateway routes (drop catch-all /{slug}/{path} proxies).
    gateway_paths = {
        p: item
        for p, item in (gateway_schema.get("paths") or {}).items()
        if "{path" not in p and "{slug" not in p
    }
    schema: dict[str, Any] = {
        "openapi": gateway_schema.get("openapi", "3.1.0"),
        "info": info,
        "paths": gateway_paths,
        "components": {
            "schemas": dict(((gateway_schema.get("components") or {}).get("schemas")) or {}),
            "securitySchemes": dict(
                ((gateway_schema.get("components") or {}).get("securitySchemes")) or {}
            ),
        },
        "tags": list(gateway_schema.get("tags") or []),
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        for agent, slug in agents.items():
            base = (upstream_fn(agent) or "").rstrip("/")
            if not base:
                schema["tags"].append(
                    {"name": slug, "description": f"{agent} — upstream URL unset"}
                )
                continue
            try:
                resp = await client.get(f"{base}/openapi.json")
                resp.raise_for_status()
                agent_spec = resp.json()
            except Exception as exc:  # noqa: BLE001
                schema["tags"].append(
                    {
                        "name": slug,
                        "description": (
                            f"{agent} — openapi unavailable ({type(exc).__name__})"
                        ),
                    }
                )
                continue

            agent_title = (agent_spec.get("info") or {}).get("title") or agent
            schema["tags"].append({"name": slug, "description": str(agent_title)})

            ns = slug.replace("-", "_") + "__"
            comps = ((agent_spec.get("components") or {}).get("schemas")) or {}
            rename = {name: f"{ns}{name}" for name in comps}
            for old, new in rename.items():
                schema["components"]["schemas"][new] = _rewrite_refs(
                    copy.deepcopy(comps[old]), rename
                )

            for path, item in (agent_spec.get("paths") or {}).items():
                prefixed = f"/{slug}{path if path.startswith('/') else '/' + path}"
                ops = _rewrite_refs(copy.deepcopy(item), rename)
                if not isinstance(ops, dict):
                    continue
                for method, op in ops.items():
                    if method.startswith("x-") or not isinstance(op, dict):
                        continue
                    tags = list(op.get("tags") or [])
                    if slug not in tags:
                        tags.insert(0, slug)
                    op["tags"] = tags
                    oid = op.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
                    op["operationId"] = f"{ns}{oid}"
                schema["paths"][prefixed] = ops

    return schema

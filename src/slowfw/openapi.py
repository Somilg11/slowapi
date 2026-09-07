"""OpenAPI 3.1 generation from the annotations you already wrote.

Nothing extra is declared: the schema comes from the same handler signatures
that drive validation, so the document cannot drift from the implementation.
If a parameter stops being required, the schema stops saying it is required, in
the same commit.
"""

from __future__ import annotations

import typing as t

from ._version import __version__
from .routing import Route
from .validation import json_schema_for

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI

__all__ = ["generate", "redoc_html", "swagger_html"]

_LOCATION_TO_IN = {"query": "query", "path": "path", "header": "header", "cookie": "cookie"}


def generate(app: SlowAPI) -> dict[str, t.Any]:
    """Build the OpenAPI document for ``app``."""
    components: dict[str, t.Any] = {}
    paths: dict[str, dict[str, t.Any]] = {}

    for route in app.routes:
        if not route.include_in_schema:
            continue
        plan = app._prepare(route)
        operation = _operation(route, plan, components)
        paths.setdefault(route.path, {})[route.method.lower()] = operation

    document: dict[str, t.Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": app.title,
            "version": app.version,
            "description": app.description or None,
            "x-generator": f"slowfw {__version__}",
        },
        "paths": paths,
    }
    document["info"] = {k: v for k, v in document["info"].items() if v is not None}
    if components:
        document["components"] = {"schemas": components}
    tags = sorted({tag for route in app.routes for tag in route.tags})
    if tags:
        document["tags"] = [{"name": tag} for tag in tags]
    return document


def _operation(route: Route, plan: t.Any, components: dict[str, t.Any]) -> dict[str, t.Any]:
    parameters: list[dict[str, t.Any]] = []
    request_body: dict[str, t.Any] | None = None

    for spec in _all_specs(plan.signature):
        if spec.source in _LOCATION_TO_IN:
            marker = spec.param
            if marker is not None and not marker.include_in_schema:
                continue
            entry: dict[str, t.Any] = {
                "name": spec.wire_name or spec.name,
                "in": _LOCATION_TO_IN[spec.source],
                "required": spec.source == "path" or spec.required,
                "schema": _constrained(json_schema_for(spec.annotation, components), marker),
            }
            if marker is not None:
                if marker.description:
                    entry["description"] = marker.description
                if marker.deprecated:
                    entry["deprecated"] = True
                if marker.examples:
                    entry["examples"] = {
                        f"example{i}": {"value": v} for i, v in enumerate(marker.examples, 1)
                    }
            parameters.append(entry)
        elif spec.source in ("body", "form", "file") and request_body is None:
            media = getattr(spec.param, "media_type", "application/json")
            schema = json_schema_for(spec.annotation, components)
            if getattr(spec.param, "embed", False):
                schema = {"type": "object", "properties": {spec.name: schema}}
            request_body = {
                "required": spec.required,
                "content": {media: {"schema": schema}},
            }

    responses: dict[str, t.Any] = {}
    success = str(route.status_code or plan.status_code)
    return_schema = json_schema_for(
        route.response_model or plan.signature.return_annotation, components
    )
    responses[success] = {
        "description": "Successful response",
        **({"content": {"application/json": {"schema": return_schema}}} if return_schema else {}),
    }
    if any(spec.required for spec in _all_specs(plan.signature)):
        responses["422"] = {"description": "Request validation failed"}
    for status, extra in route.responses.items():
        responses[str(status)] = extra

    operation: dict[str, t.Any] = {
        "operationId": route.name,
        "responses": responses,
    }
    if route.summary or route.description:
        operation["summary"] = route.summary or (route.description or "").split("\n")[0]
    if route.description:
        operation["description"] = route.description
    if route.tags:
        operation["tags"] = list(route.tags)
    if route.deprecated:
        operation["deprecated"] = True
    if parameters:
        operation["parameters"] = parameters
    if request_body is not None:
        operation["requestBody"] = request_body
    return operation


def _all_specs(signature: t.Any) -> t.Iterator[t.Any]:
    """Yield the handler's own params plus everything its dependencies need."""
    for spec in signature.specs:
        if spec.source == "depends" and spec.sub is not None:
            yield from _all_specs(spec.sub)
        else:
            yield spec


def _constrained(schema: dict[str, t.Any], marker: t.Any) -> dict[str, t.Any]:
    """Fold a :class:`~slowfw.params.Param`'s constraints into its schema."""
    if marker is None:
        return schema
    mapping = {
        "ge": "minimum",
        "le": "maximum",
        "gt": "exclusiveMinimum",
        "lt": "exclusiveMaximum",
        "min_length": "minLength",
        "max_length": "maxLength",
        "pattern": "pattern",
    }
    for attribute, keyword in mapping.items():
        value = getattr(marker, attribute, None)
        if value is not None:
            schema[keyword] = value
    if marker.title:
        schema["title"] = marker.title
    if not marker.required:
        default = marker.get_default()
        if default is not None:
            schema["default"] = default
    return schema


_DOC_STYLE = """
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
    #swagger-ui, #redoc { min-height: 100vh; }
    .slowfw-offline { padding: 3rem; max-width: 40rem; margin: 0 auto; line-height: 1.6; }
  </style>
"""


def swagger_html(openapi_url: str, title: str) -> str:
    """Swagger UI page.  Assets load from a CDN; see the note in the markup."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — API reference</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  {_DOC_STYLE}
</head>
<body>
  <div id="swagger-ui"></div>
  <noscript class="slowfw-offline">
    This page renders the schema at <code>{openapi_url}</code> with Swagger UI, which is
    loaded from a CDN. In an air-gapped environment, fetch that URL directly or
    run <code>python -m slowfw openapi</code>.
  </noscript>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js" crossorigin></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: {openapi_url!r},
      dom_id: '#swagger-ui',
      deepLinking: true,
      persistAuthorization: true,
      tryItOutEnabled: true
    }});
  </script>
</body>
</html>"""


def redoc_html(openapi_url: str, title: str) -> str:
    """ReDoc page, for a reference-style read of the same document."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — API reference</title>
  {_DOC_STYLE}
</head>
<body>
  <redoc spec-url="{openapi_url}"></redoc>
  <script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js" crossorigin></script>
</body>
</html>"""

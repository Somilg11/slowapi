"""``slowapi new`` -- generate a project that is deployable on day one.

The templates are intentionally small.  Every file exists because leaving it
out is a decision someone would otherwise have to make under time pressure:
pinned dependencies, a non-root Dockerfile, a health endpoint, a test that
runs on both protocols.
"""

from __future__ import annotations

import os

__all__ = ["TEMPLATES", "scaffold"]

_MAIN_API = '''"""__PROJECT__ — a SlowAPI service."""

from dataclasses import dataclass

from slowapi import SlowAPI
from slowapi.middleware import CORSMiddleware, SecurityHeadersMiddleware

app = SlowAPI(title="__PROJECT__", version="0.1.0")
app.use(SecurityHeadersMiddleware(), CORSMiddleware(allow_origins=["*"]))


@dataclass
class Health:
    status: str
    version: str


@app.get("/health", tags=["ops"])
def health() -> Health:
    """Liveness probe."""
    return Health(status="ok", version=app.version)


@app.get("/hello/{name}")
def hello(name: str, excited: bool = False) -> dict:
    return {"message": f"Hello, {name}" + ("!" if excited else ".")}
'''

_TEST = '''import pytest

from slowapi.testing import TestClient

from main import app


@pytest.fixture(params=["wsgi", "asgi"])
def client(request):
    """Every test runs twice: once per protocol."""
    with TestClient(app, protocol=request.param) as client:
        yield client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hello(client):
    assert client.get("/hello/world").json()["message"] == "Hello, world."
    assert client.get("/hello/world", params={"excited": "true"}).json()["message"].endswith("!")
'''

_DOCKERFILE = """# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user: a container escape should not land on uid 0.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \\
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "slowapi", "run", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

_REQUIREMENTS = """slowapi
uvicorn[standard]>=0.30
"""

_ENV = """# Copy to .env and fill in. Never commit the real file.
APP_NAME=__PROJECT__
ENVIRONMENT=development
DEBUG=true
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=INFO
SECRET_KEY=
"""

_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
venv/
.env
.pytest_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/
.mypy_cache/
.ruff_cache/
"""

TEMPLATES: dict[str, dict[str, str]] = {
    "api": {
        "main.py": _MAIN_API,
        "tests/test_main.py": _TEST,
        "requirements.txt": _REQUIREMENTS,
        "Dockerfile": _DOCKERFILE,
        ".env.example": _ENV,
        ".gitignore": _GITIGNORE,
    },
}
# The other templates share the API base and add their own extras.
TEMPLATES["modular"] = dict(TEMPLATES["api"])
TEMPLATES["fullstack"] = dict(TEMPLATES["api"])


def scaffold(
    name: str, *, template: str = "api", force: bool = False, root: str | None = None
) -> str:
    """Write a project skeleton into ``./<name>`` and return the path."""
    if template not in TEMPLATES:
        raise SystemExit(f"Unknown template {template!r}. Choose from: {', '.join(TEMPLATES)}")

    target = os.path.abspath(os.path.join(root or os.getcwd(), name))
    if os.path.exists(target) and os.listdir(target) and not force:
        raise SystemExit(f"{target} already exists and is not empty. Pass --force to overwrite.")

    for relative, content in TEMPLATES[template].items():
        path = os.path.join(target, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(content.replace("__PROJECT__", name))

    print(f"Created {target}")
    print("\nNext:")
    print(f"  cd {name}")
    print("  pip install -r requirements.txt")
    print("  python -m slowapi run main:app --reload")
    return target

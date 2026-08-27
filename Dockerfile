# syntax=docker/dockerfile:1.7
#
# Multi-stage so the runtime image carries no build toolchain and no wheels
# cache. The result is small, and the attack surface is roughly "python plus
# your code".

# ---------------------------------------------------------------- builder ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the build backend needs first, so dependency layers cache.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip build && pip install ".[asgi]"

# ---------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=production

# A container escape should not land on uid 0.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app examples/rest-api ./

USER app
EXPOSE 8000

# The health check hits the app's own endpoint, so an orchestrator learns about
# a wedged event loop rather than only about a dead process.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "slowapi", "run", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--server", "uvicorn", "--json-logs"]

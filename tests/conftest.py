"""Shared fixtures.

The ``client`` fixture is parameterised over both protocols, so every test
written against it is automatically a dual-protocol test.  That is the single
most important convention in this suite: a change that works on WSGI but
breaks ASGI (or the reverse) fails here rather than in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `pytest` to run from a clean checkout without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slowfw import SlowAPI
from slowfw.logging import configure_logging
from slowfw.testing import TestClient

PROTOCOLS = ["wsgi", "asgi"]


@pytest.fixture(autouse=True, scope="session")
def _quiet_logs() -> None:
    configure_logging("CRITICAL")


@pytest.fixture(params=PROTOCOLS)
def protocol(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def make_client(protocol: str):
    """Return a factory that wraps an app in a client for the active protocol."""

    def factory(app: SlowAPI, **kwargs) -> TestClient:
        return TestClient(app, protocol=protocol, **kwargs)

    return factory


@pytest.fixture
def app() -> SlowAPI:
    """A bare application with docs routes disabled to keep tables small."""
    return SlowAPI(title="test", docs_url=None, redoc_url=None, openapi_url=None)


@pytest.fixture
def client(app: SlowAPI, make_client):
    return make_client(app)

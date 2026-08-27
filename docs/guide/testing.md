# Testing

`TestClient` calls the application in-process — no socket, no server, no port.
That makes tests fast, but the reason it exists is coverage: the same test can
be run through the WSGI adapter and the ASGI adapter, and a change that works
on one but not the other fails immediately.

## The pattern to copy

```python
# conftest.py
import pytest

from slowapi.testing import TestClient

from myapp.main import app


@pytest.fixture(params=["wsgi", "asgi"])
def client(request):
    """Every test using this fixture runs twice, once per protocol."""
    with TestClient(app, protocol=request.param) as c:
        yield c
```

```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

Adopt this on day one. Retrofitting it later means auditing every test you
already wrote.

## Making requests

```python
client.get("/users", params={"limit": 10})
client.post("/users", json={"email": "a@b.c"})
client.post("/login", data={"username": "x", "password": "y"})     # form-encoded
client.post("/raw", data=b"bytes", headers={"content-type": "application/octet-stream"})
client.patch("/users/1", json={"done": True})
client.delete("/users/1")
client.head("/users")
client.options("/users", headers={"origin": "https://app.example"})
```

## Reading responses

```python
response.status_code
response.json()
response.text
response.content                    # bytes
response.headers["content-type"]    # case-insensitive
response.cookies                    # parsed Set-Cookie
response.ok                         # 2xx or 3xx
response.raise_for_status()         # AssertionError with the body, if not ok
```

## Cookies and sessions

Cookies persist across requests on the same client, so a login flow is just two
calls:

```python
def test_session_survives(client):
    client.post("/login", data={"email": "a@b.c"})
    assert client.get("/me").json()["email"] == "a@b.c"
```

Disable with `TestClient(app, follow_cookies=False)`.

## Startup and shutdown

Using the client as a context manager runs the lifecycle, which is what builds
singletons and runs `on_module_init`:

```python
with TestClient(app) as client:
    ...
```

Without it, startup still happens lazily on the first request, but shutdown
never runs — so use the context manager whenever a test asserts on teardown.

## Replacing dependencies

Registering a provider again replaces it:

```python
class FakeEmail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject):
        self.sent.append((to, subject))


@pytest.fixture
def fake_email(app):
    fake = FakeEmail()
    app.provide(Provider.value(EmailService, fake))
    return fake


def test_signup_sends_a_welcome(client, fake_email):
    client.post("/signup", json={"email": "a@b.c"})
    assert fake_email.sent == [("a@b.c", "Welcome")]
```

For `Depends`-based dependencies, prefer designing them to read from a provider
so they can be swapped the same way.

## Testing units directly

Not everything needs a request:

```python
def test_pagination_bounds():
    with pytest.raises(FieldError):
        validate_param(500, Query(20, le=100), ("query", "limit"))


def test_router_prefers_literals():
    router = Router()
    router.add(Route("/users/me", "GET", handler, "me"))
    router.add(Route("/users/{id:int}", "GET", handler, "by_id"))
    assert router.match("GET", "/users/me")[0].name == "me"
```

## Asserting parity explicitly

For anything touching dispatch, request parsing, or response writing, assert
that the two protocols agree rather than testing them separately:

```python
def test_identical_on_both_protocols():
    wsgi = TestClient(app, protocol="wsgi")
    asgi = TestClient(app, protocol="asgi")
    a, b = wsgi.get("/users/1"), asgi.get("/users/1")
    assert a.status_code == b.status_code
    assert a.content == b.content
```

## Asserting the fast path

The performance claim is testable, so test it:

```python
from slowapi.concurrency import _LoopThread, shutdown_loop_thread


def test_sync_wsgi_never_starts_a_loop():
    shutdown_loop_thread()
    TestClient(app, protocol="wsgi").get("/plain")
    assert _LoopThread._instance is None
```

If a refactor accidentally makes a framework internal `async def` in a way that
suspends, this catches it.

## Running the suite

```bash
make test                                   # everything
pytest tests/unit -q                        # fast unit tests
pytest -k "wsgi"                            # one protocol
pytest --cov --cov-report=term-missing      # coverage
```

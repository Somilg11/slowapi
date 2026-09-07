# Contributing to SlowAPI

Thanks for considering it. This document is short on ceremony and specific
about the two things that actually get pull requests rejected.

## The two rules

### 1. Tests run on both protocols

SlowAPI's entire premise is that WSGI and ASGI behave identically. A test that
only exercises one is not testing the framework, it is testing half of it.

```python
@pytest.fixture(params=["wsgi", "asgi"])
def client(request):
    with TestClient(app, protocol=request.param) as c:
        yield c
```

Use the `client` or `make_client` fixture from `tests/conftest.py` and your
test runs twice automatically. For anything touching dispatch, request parsing,
or response writing, assert parity directly:

```python
a, b = wsgi_client.get("/x"), asgi_client.get("/x")
assert a.status_code == b.status_code and a.content == b.content
```

### 2. No new required runtime dependencies

The core installs with nothing, and CI proves it on every commit. If your
feature needs a library:

- Put it in `slowfw/contrib/` behind an optional extra, import it lazily, and
  raise a clear error when it is missing; **or**
- Implement the capability with the standard library; **or**
- Make the case in an issue first. It has to be a strong one.

## Getting set up

```bash
git clone https://github.com/Somilg11/slowfw && cd slowfw
make install          # .venv with -e ".[dev,all]"
make check            # lint + types + tests, exactly what CI runs
```

Useful targets: `make test`, `make cov`, `make lint`, `make format`,
`make typecheck`, `make bench`.

Optionally: `pre-commit install`, so the same checks run before each commit.

## The invariants

Every change is measured against these. They are explained at length in
[docs/growth.md](docs/growth.md#2-the-invariants).

1. **Protocol parity** — identical behaviour on WSGI and ASGI.
2. **Zero required dependencies** — extras only.
3. **The fast path stays loop-free** — no framework-internal `await` that can
   suspend in the common synchronous path.
4. **Analyse at registration** — nothing in the hot path calls
   `inspect.signature` or `get_type_hints`.
5. **Fail at import, not at 3am** — configuration errors raise at startup.
6. **Errors name the fix** — not just what is wrong, what to do.
7. **Both response styles stay first-class** — `res` mutation and returned
   values, forever.
8. **Additions are opt-in** — existing applications keep working unchanged.

Invariant 3 is the one contributors trip over. If you add an `await` to the
dispatch path, ask whether it can actually suspend. If it can, it must be
reachable only when the route was classified as non-synchronous.

## What makes a good pull request

**Start with an issue** for anything beyond a bug fix or a docs correction. A
short discussion saves a long rewrite.

**Describe the situation, not just the API.** "I tried to do X and the
framework made it hard, so I did Y instead" is the most useful thing you can
write. It is the input the roadmap is built from.

**One change per pull request.** A bug fix and a refactor in one diff is two
reviews wearing a trenchcoat.

**Tests that fail before and pass after.** For a bug fix, write the failing
test first and include it.

**Documentation in the same commit.** A public API change that does not touch
`docs/` is incomplete. So is one that does not add a `## [Unreleased]` entry in
`CHANGELOG.md`.

## Code style

Enforced by `ruff` — run `make format` and stop thinking about it. Beyond
formatting:

**Type-annotate public functions.** `mypy` runs in CI over `src/slowfw`.

**Write docstrings that say why, not what.** The signature already says what.

```python
# Not useful
def drive(coro):
    """Drive a coroutine."""

# Useful
def drive(coro):
    """Run a coroutine to completion in the calling thread, with no event loop.

    A coroutine only suspends when it awaits something not yet done. A dispatch
    chain in which every participant is an ordinary `def` never does that, so
    stepping it with send(None) runs it straight through.
    """
```

**Comment the surprising, not the obvious.** If a line looks wrong but is
right, say why. If it looks right and is right, say nothing.

**Error messages name the fix.**

```python
# Not this
raise ConfigurationError("Invalid middleware")

# This
raise ConfigurationError(
    f"Middleware {name!r} was passed as a class. Instantiate it ({name}()) "
    "or register it as a provider first."
)
```

**Match the surrounding code.** Comment density, naming, and idiom included.

## Project layout

```
src/slowfw/          the framework
├── adapters/         the only files that know about WSGI or ASGI
├── middleware/       built-in middleware
tests/unit/           no HTTP; fast
tests/integration/    through TestClient, on both protocols
docs/                 the guide, internals, reference
examples/             five runnable applications
benchmarks/           make bench
```

The dependency rule: `adapters/` imports `app.py`, `app.py` imports everything
else, and **nothing below `app.py` imports from `adapters/`**. That is what
keeps protocol knowledge confined to two files.

## Reporting bugs

Use the issue template, and include a runnable `TestClient` snippet. A report
with one is a test we can merge; a report without one is a conversation.

State which protocol shows the bug — WSGI, ASGI, or both. That single field
often localises the cause immediately.

## Reporting vulnerabilities

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## Proposing features

Open a discussion or an issue and cover:

1. The situation you are in, before the API you want.
2. What you tried instead, and why it was not enough.
3. How it works on **both** protocols.
4. Whether it needs a new dependency.

Points 3 and 4 are not paperwork — they are usually where the design happens.

## Releasing (maintainers)

1. Update `CHANGELOG.md`: move `## [Unreleased]` to the new version.
2. Bump `__version__` in `src/slowfw/_version.py`.
3. `make check`.
4. Tag `vX.Y.Z` and push. The release workflow verifies the tag matches the
   package version, builds, and publishes to PyPI via trusted publishing.

## Dependency updates and notification noise

Dependabot is configured in `.github/dependabot.yml` for quiet rather than
currency: monthly, one grouped pull request per ecosystem, a limit of one open
at a time, and major bumps ignored. Security updates are deliberately left out
of that file — they are a separate always-on repository setting, and they are
the alerts worth receiving.

That file controls how many pull requests appear. It cannot control whether you
are emailed about them, because notification delivery is a per-account setting.
If Dependabot is filling your inbox, the two switches are:

- **Repository → Watch → Custom**, and untick *Pull requests*. You keep releases
  and issues; you stop hearing about every bump.
- **<https://github.com/settings/notifications>**, under *Dependabot alerts*,
  untick **Email**. Alerts still appear in the security tab and in the web
  notification inbox.

Neither is in this repository, so neither can be changed by a pull request.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions are licensed under the MIT License, matching the project.

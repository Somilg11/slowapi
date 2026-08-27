## What this changes

<!-- One or two sentences. What behaviour is different after this PR? -->

## Why

<!-- Link the issue, or explain the problem. "Because it's cleaner" needs a
     concrete example of what got harder before this change. -->

Closes #

## Type of change

- [ ] Bug fix (no API change)
- [ ] New feature (no API change)
- [ ] Breaking change (existing code stops working)
- [ ] Documentation
- [ ] Internal / refactor

## Checklist

- [ ] `make check` passes locally
- [ ] Tests cover the new behaviour, and they run on **both** protocols
- [ ] Public API changes are documented in `docs/`
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`
- [ ] No new required runtime dependency (see `docs/contributing.md` if you need one)

## Dual-protocol confirmation

<!-- Delete if not applicable. Anything touching dispatch, request, response,
     or the adapters must state how it was verified on both. -->

- [ ] Verified under WSGI (`TestClient(app, protocol="wsgi")`)
- [ ] Verified under ASGI (`TestClient(app, protocol="asgi")`)

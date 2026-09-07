# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1 | No |

Before 1.0, security fixes land on the latest minor version only. After 1.0,
this table will list a maintained range.

## Reporting a vulnerability

**Please do not open a public issue.**

Use GitHub's private reporting:
[Report a vulnerability](https://github.com/Somilg11/slowfw/security/advisories/new)

Include:

- The type of issue (injection, traversal, auth bypass, DoS, …)
- Affected files and versions
- Steps to reproduce, ideally as a `TestClient` snippet
- Which protocol it affects — WSGI, ASGI, or both
- Impact: what an attacker gains

### What to expect

| | |
| --- | --- |
| Acknowledgement | within 72 hours |
| Initial assessment | within 7 days |
| Fix or mitigation plan | within 30 days for confirmed issues |
| Public disclosure | after a fix ships, coordinated with you |

Credit is given in the advisory and the changelog unless you prefer otherwise.

## Scope

### In scope

- Path traversal in static file serving or template resolution
- Request smuggling, header injection, response splitting
- Cross-site scripting through the template engine's escaping
- Session forgery, signature bypass, timing attacks in `Signer`
- Denial of service through parsing (multipart, JSON, query strings, routing)
- Authorisation bypass in guards or middleware ordering
- Information disclosure in error responses or logs
- Any case where a security default documented in
  [docs/guide/security.md](docs/guide/security.md) does not hold

### Out of scope

- Vulnerabilities in applications built with SlowAPI, unless the framework made
  the insecure pattern the default or the documented one
- Anything requiring `debug=True`, which is documented as development-only
- Rendering a template whose **source** came from a request — documented as
  remote code execution, in any engine
- Missing hardening that the docs state is opt-in (CORS, HSTS, trusted hosts)
  when it was not opted into
- Vulnerabilities in optional dependencies — report those upstream
- Rate-limit inaccuracy across workers with the in-memory store, which is
  documented behaviour

## Security-relevant defaults

Documented in full in [docs/guide/security.md](docs/guide/security.md). A change
that weakens any of them is treated as a vulnerability:

- Tracebacks never reach clients unless `debug=True`
- Templates HTML-escape every interpolation
- Cookies default to `HttpOnly` and `SameSite=Lax`
- `SameSite=None` without `Secure` raises rather than being silently sent
- Static paths are resolved and confined to the mounted root; symlinks out are
  refused
- Request bodies are capped, enforced while reading and not only from the header
- `X-Forwarded-*` is ignored unless the peer is a declared trusted hop
- Sessions use HMAC-SHA256 with constant-time comparison and per-purpose salts
- `SECRET_KEY` is required in production; `require_secret()` refuses to start
  without one

## Supply chain

SlowAPI has **zero required runtime dependencies**, and CI verifies that on
every commit by installing with no extras and importing the package. This is a
deliberate security property: the shortest supply chain is no supply chain.

Optional extras are pinned to minimum versions and monitored by Dependabot.
CodeQL runs on every push and weekly.

# Security

What SlowAPI does for you, what it does only if you ask, and what remains
yours. The line matters: a framework that claims to be "secure by default"
without saying where the default stops is not helping.

## On by default

| Behaviour | Where |
| --- | --- |
| Tracebacks never reach the client (unless `debug=True`) | `ErrorMiddleware` |
| Templates HTML-escape every interpolation | `TemplateEngine` |
| Cookies are `HttpOnly` and `SameSite=Lax` | `Response.cookie` |
| `SameSite=None` without `Secure` raises | `Response.cookie` |
| Static file paths are resolved and confined to the root | `StaticFiles` |
| Symlinks out of the static root are refused | `StaticFiles` |
| Request bodies capped at 16 MB | `Request` |
| Multipart bodies capped at 1000 parts | `formparsers` |
| Uploaded temp files closed at request end | request scope |
| `X-Forwarded-*` ignored unless the hop is trusted | `Request.ip` |
| Sessions signed with HMAC-SHA256, compared in constant time | `Signer` |
| Session cookies over the browser size limit raise | `SessionMiddleware` |
| Every response carries a correlation id | `RequestIDMiddleware` |
| Zero required runtime dependencies | by design |

That last one is a security property. Every dependency is code you ship,
audit, and patch; the shortest supply chain is no supply chain.

## Opt-in, and you should

```python
app.use(
    ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]),
    TrustedHostMiddleware(["api.example.com"]),
    SecurityHeadersMiddleware(hsts_seconds=31_536_000),
    CORSMiddleware(allow_origins=["https://app.example.com"], allow_credentials=True),
    RateLimitMiddleware(limit=600, window=60),
    SessionMiddleware(settings.require_secret(), secure=True),
)
```

These are opt-in because each one needs a value only you know — your hosts,
your origins, your proxy network. A default would either be wrong or be `"*"`,
and `"*"` is how CORS bugs happen.

## Still yours

SlowAPI does not provide, and does not pretend to provide:

- **Authentication.** No user model, no password hashing, no JWT library. Use
  `argon2-cffi` or `bcrypt` for passwords and `PyJWT` for tokens, and wire them
  through a [guard](guards-interceptors-pipes.md).
- **CSRF protection.** Sessions are `SameSite=Lax`, which covers most of it for
  a same-origin app. A form-heavy application should still use a synchroniser
  token. On the roadmap; see [growth.md](../growth.md).
- **SQL injection defence.** Use parameterised queries or an ORM. No framework
  can save a hand-built `f"SELECT ... WHERE id = {value}"`.
- **Authorisation policy.** Guards give you the hook and the metadata
  vocabulary; the rules are yours.
- **Secret storage.** Read secrets from the environment, populated by a secret
  manager.

## Notes on specific defences

### CORS

`allow_origins=["*"]` with `allow_credentials=True` raises at construction —
browsers reject that combination, so failing at import beats debugging it in
devtools. `Vary: Origin` is always set so a shared cache cannot serve one
origin's response to another.

### Proxy headers

Reading `X-Forwarded-For` unconditionally lets any client forge its own address,
which silently breaks rate limiting, audit logs, and IP allow-lists.
`req.ip` only consults it when `ProxyHeadersMiddleware` has confirmed the
immediate peer is in your trusted network.

### Templates

Interpolation escapes by default; opting out requires `| safe`, which is
greppable in review. But: **never render a template whose source came from a
request.** Template source is code. User *data* rendered through a template is
exactly what the escaping is for and is entirely safe.

### Sessions

Signed, not encrypted. The client can read the contents. Store an identifier,
not a secret. Rotate keys with `fallback_secrets=[old]` so verification keeps
working while new cookies use the new key.

### Rate limiting

The default store is in-process: exact for one worker, approximate across
several. If you run four workers, a limit of 100 is effectively up to 400.
Implement `RateLimitStore` against Redis for anything that must be exact.

### Error messages

`HTTPException` details are shown to clients, so do not put internals in them:

```python
raise NotFound("No user with id 42")                       # fine
raise NotFound(f"SELECT failed on shard-3: {exc}")         # leaks infrastructure
```

## A hardened baseline

```python
settings = Settings.load()

app = SlowAPI(
    title=settings.app_name,
    debug=False,
    docs_url="/docs" if settings.show_docs else None,
    openapi_url="/openapi.json" if settings.show_docs else None,
    max_body_size=4 * 1024 * 1024,
)

app.use(
    ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]),
    TrustedHostMiddleware(["api.example.com"]),
    SecurityHeadersMiddleware(
        hsts_seconds=31_536_000,
        content_security_policy="default-src 'self'; frame-ancestors 'none'",
    ),
    AccessLogMiddleware(),
    CORSMiddleware(allow_origins=["https://app.example.com"], allow_credentials=True),
    RateLimitMiddleware(limit=600, window=60, key=lambda r: r.get("x-api-key") or r.ip),
    SessionMiddleware(settings.require_secret(), secure=True, samesite="lax"),
)
```

## Reporting a vulnerability

Please do not open a public issue. See [SECURITY.md](../../SECURITY.md).

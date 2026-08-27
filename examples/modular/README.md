# modular

The same API as `rest-api/`, organised the way a team of ten would want it.

```bash
python -m slowapi run main:app
```

```bash
curl localhost:8000/users                      # public, emails hidden
curl localhost:8000/users/directory            # 401 - needs a role
curl localhost:8000/users/directory -H 'authorization: Bearer admin'
curl -X POST localhost:8000/users -H 'authorization: Bearer admin' \
     -H 'content-type: application/json' -d '{"name":"Linus","email":"l@x.io"}'
curl localhost:8000/audit
```

## What to notice

- **`@public` and `@roles("admin")` decide nothing.** They record metadata.
  `RoleGuard` reads it and decides. Swap the guard, keep every annotation.
- **The guard is injectable.** It receives `UserService` through its
  constructor, so policy can query real data instead of duplicating it.
- **`/users` and `/users/directory` return the same objects.** The difference
  is `@serialize_with(groups=("admin",))` — one decorator, not a second DTO
  and a mapping function.
- **`UserModule` exports `UserService` and nothing else.** Exporting something
  the module does not provide fails at import time, not at 3am.
- **`@app.get("/audit")` injects `Auditor` by annotation.** Function handlers
  and controllers draw from the same container.

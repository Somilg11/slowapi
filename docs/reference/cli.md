# Command line

```bash
python -m slowfw <command>      # always available
slowapi <command>                # after installation, via the entry point
```

## `run`

Start a server.

```bash
slowapi run main:app --reload
slowapi run main:app --host 0.0.0.0 --port 8080 --workers 4 --json-logs
slowapi run main:app --server gunicorn
```

| Option | Default | Notes |
| --- | --- | --- |
| `app` | — | `module:attribute`; bare `module` means `module:app` |
| `--host` | `127.0.0.1` | Use `0.0.0.0` in a container |
| `--port` | `8000` | |
| `--server` | `auto` | `auto`, `uvicorn`, `gunicorn`, `wsgiref` |
| `--reload` | off | Development only |
| `--workers` | `1` | |
| `--log-level` | `info` | |
| `--json-logs` | off | One JSON object per line |

`auto` prefers uvicorn, then gunicorn, then the standard library's `wsgiref`
(with a warning that it is single-threaded). That fallback is why `app.run()`
works in a virtualenv with nothing but SlowAPI installed.

## `routes`

Print the route table. The fastest way to answer "why is this 404".

```bash
$ slowapi routes main:app
METHOD   PATH                  NAME
--------------------------------------------
DELETE   /tasks/{task_id}      delete_task
GET      /health               health
GET      /tasks                list_tasks
GET      /tasks/{task_id}      get_task
PATCH    /tasks/{task_id}      update_task
POST     /tasks                create_task

6 route(s)
```

`--json` emits machine-readable output, which is useful for asserting in CI
that a route was not removed by accident.

## `check`

Analyse every route without starting a server.

```bash
slowapi check main:app
```

```
OK    14 route(s) validated
```

This resolves every handler signature, every `Depends` chain, every guard,
interceptor and pipe, and every provider a handler injects. A problem is
reported with the route and handler that owns it:

```
FAIL  2 route(s) failed validation:
  GET /reports/{id} (get_report): Could not resolve type hints for 'get_report':
    name 'ReportService' is not defined. Check for forward references to names
    that are not importable at runtime.
  POST /items (create_item): Parameter 'body' declares Body() inside Annotated[...]
    and Query() as its default. Pick one.
```

Every broken route is listed, not just the first — fixing one typo only to meet
the next one on the following run is a poor use of a deploy cycle.

Run it in CI next to the linter. The same analysis runs automatically at
startup, so a broken route already fails the process rather than the first
request; `check` moves the discovery earlier still, to a red build.

```yaml
- run: slowapi check main:app
```

Exit code `1` on failure, `0` on success.

## `openapi`

Dump the generated schema.

```bash
slowapi openapi main:app                      # to stdout
slowapi openapi main:app -o openapi.json      # to a file
```

Commit the output and diff it in CI to catch unintended API changes:

```bash
slowapi openapi main:app -o /tmp/current.json
diff <(jq -S . openapi.json) <(jq -S . /tmp/current.json)
```

## `secret`

Generate a cryptographically strong key for `SECRET_KEY`.

```bash
$ slowapi secret
kPvNc7xQ2mYbF8dR3wLzJ5nT1hA6sE9uG4iO0pXvC2kM7bN3

$ slowapi secret --bytes 64
```

Uses `secrets.token_urlsafe`. Never reuse a key between environments, and never
commit one.

## `new`

Scaffold a project that is deployable on day one.

```bash
slowapi new my-service
slowapi new my-service --template modular
```

Templates: `api` (default), `modular`, `fullstack`.

Generated:

```
my-service/
├── main.py             # a health endpoint and one route
├── tests/test_main.py  # runs on both protocols
├── requirements.txt
├── Dockerfile          # multi-stage, non-root, health-checked
├── .env.example
└── .gitignore
```

Every file exists because leaving it out is a decision someone would otherwise
have to make under time pressure.

## Exit codes

`0` success, `1` failure, `2` bad arguments. Import failures print the module
that could not be imported rather than a bare traceback.

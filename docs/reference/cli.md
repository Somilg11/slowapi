# Command line

```bash
python -m slowapi <command>      # always available
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

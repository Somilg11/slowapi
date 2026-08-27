# Examples

Each directory is a complete, runnable application. They are ordered by how
much of the framework they use, not by how impressive they look.

| Example | What it shows | Run it |
| --- | --- | --- |
| [`hello/`](hello) | The smallest useful app, and the dual-protocol claim in ten lines | `python -m slowapi run main:app` |
| [`rest-api/`](rest-api) | A typed CRUD service: DTOs, validation, dependencies, OpenAPI | `python -m slowapi run main:app --reload` |
| [`modular/`](modular) | NestJS-shaped: modules, controllers, DI, guards, interceptors | `python -m slowapi run main:app` |
| [`fullstack/`](fullstack) | Server-rendered pages, sessions, static files, forms | `python -m slowapi run main:app` |
| [`async-stream/`](async-stream) | Streaming, background work, and mixing `def` with `async def` | `uvicorn main:app` |

Every example runs under both protocols. Try the same app twice:

```bash
gunicorn main:app --workers 4     # WSGI
uvicorn  main:app                 # ASGI
```

The responses are byte-identical. That is the point.

# async-stream

```bash
pip install "slowapi-framework[asgi]"
uvicorn main:app
```

```bash
curl -N localhost:8000/sse?count=5                  # watch events arrive
curl -s localhost:8000/ndjson?rows=5
curl -s "localhost:8000/concurrent?n=20" | jq       # elapsed stays ~50ms
curl -OJ localhost:8000/download?size_kb=256
curl -X POST "localhost:8000/notify?email=a@b.c" -i # 202, work continues after
```

## What to notice

- **`/sse` and `/ndjson` are both streams**, but one is an async generator and
  the other an ordinary one. SlowAPI drives whichever you wrote.
- **`/concurrent?n=20` takes about 50ms, not a second.** That is what `async
  def` buys, and why the framework does not force it on handlers that would
  gain nothing.
- **`/blocking` does not stall the server under ASGI.** It is offloaded to a
  thread. Under WSGI it runs inline, because a sync worker is already the right
  place for it.
- **`res.background` runs after the response is flushed**, so the client is not
  waiting on it.

Run the same file under `gunicorn main:app --workers 2` and every endpoint
still works — including the async ones.

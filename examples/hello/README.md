# hello

```bash
pip install slowapi-framework
python -m slowapi run main:app
```

Then:

```bash
curl localhost:8000/
curl "localhost:8000/greet/ada?excited=true"
curl "localhost:8000/add?a=2&b=3"
curl "localhost:8000/add?a=oops"        # 422, with the reason
open  localhost:8000/docs
```

## Prove the dual-protocol claim

```bash
pip install gunicorn uvicorn

gunicorn main:app --bind :8000 &   # WSGI
curl -s localhost:8000/add?a=1&b=2 ; kill %1

uvicorn main:app --port 8000 &     # ASGI
curl -s localhost:8000/add?a=1&b=2 ; kill %1
```

Identical output, two entirely different servers, one unchanged file.

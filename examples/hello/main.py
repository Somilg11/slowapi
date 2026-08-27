"""The smallest interesting SlowAPI application.

Three handlers, written three different ways, all in one app:

* ``greet`` is Express-shaped -- it mutates ``res`` and returns nothing.
* ``add`` is FastAPI-shaped -- it takes typed arguments and returns a value.
* ``slow`` is ``async def`` -- and still runs under a WSGI server.

Run it either way and compare:

    python -m slowapi run main:app          # picks the best server installed
    gunicorn main:app --workers 4           # WSGI
    uvicorn  main:app                       # ASGI
"""

import asyncio

from slowapi import SlowAPI

app = SlowAPI(title="Hello", version="1.0.0")


@app.get("/")
def index(req, res):
    """Express style: mutate the response object."""
    res.json(
        {
            "message": "Hello from SlowAPI",
            "protocol": req.scope.get("protocol"),
            "docs": "/docs",
        }
    )


@app.get("/greet/{name}")
def greet(name: str, excited: bool = False, res=None):
    """A path parameter and a typed query parameter."""
    res.text(f"Hello, {name}" + ("!" if excited else "."))


@app.get("/add")
def add(a: int, b: int) -> dict:
    """FastAPI style: return a value and let the framework serialise it.

    ``a`` and ``b`` arrive as text on the wire and reach the function as ints;
    ``/add?a=oops`` never gets here, it gets a 422 describing the problem.
    """
    return {"sum": a + b}


@app.get("/slow")
async def slow(seconds: float = 0.1) -> dict:
    """An async handler.

    Under uvicorn this awaits on the event loop. Under gunicorn's sync workers
    it runs on SlowAPI's shared background loop -- same code, no rewrite.
    """
    await asyncio.sleep(min(seconds, 2.0))
    return {"slept": seconds}


if __name__ == "__main__":
    app.run(port=8000)

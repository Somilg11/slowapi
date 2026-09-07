# Installation

```bash
pip install slowfw
```

That is the whole runtime. SlowAPI has **zero required dependencies** — no
Pydantic, no Starlette, no `anyio`, no `click`. The import name is `slowfw`:

```python
from slowfw import SlowAPI
```

> **On the name.** `slowapi` on PyPI is an unrelated rate-limiting library for
> Starlette, which is why this project is not called that. `slowfw` is the
> distribution, the import and the command, so the two can share an environment
> without either noticing.

## Supported versions

| | |
| --- | --- |
| Python | 3.10, 3.11, 3.12, 3.13 |
| Operating systems | Linux, macOS, Windows |
| Implementations | CPython (PyPy is untested, not unsupported) |

Python 3.10 is the floor because SlowAPI resolves `X | Y` annotations at
runtime, which `typing.get_type_hints` only handles from 3.10 onwards.

## Optional extras

Extras are opt-in on purpose: a dependency you did not ask for is a dependency
you still have to patch.

```bash
pip install "slowfw[asgi]"       # uvicorn, for the ASGI path
pip install "slowfw[wsgi]"       # gunicorn, for the WSGI path
pip install "slowfw[templates]"  # jinja2, instead of the built-in engine
pip install "slowfw[pydantic]"   # pydantic models as DTOs
pip install "slowfw[all]"        # all of the above
pip install "slowfw[dev]"        # plus pytest, ruff, mypy
```

Nothing above changes how you write code. `app.run()` works with none of them
installed, falling back to the standard library's `wsgiref` server (and telling
you it did).

| Extra | Enables |
| --- | --- |
| `asgi` | `uvicorn main:app`, and `app.run()` picking uvicorn |
| `wsgi` | `gunicorn main:app`, and `app.run()` picking gunicorn |
| `templates` | `app.configure_templates(..., engine="jinja2")` |
| `pydantic` | Pydantic models anywhere a dataclass DTO is accepted |

## Verify the install

```bash
python -m slowfw --version
python -c "import slowfw; print(slowfw.__version__)"
```

## From source

```bash
git clone https://github.com/Somilg11/slowfw
cd slowfw
make install     # creates .venv and installs -e ".[dev,all]"
make check       # lint, types, and the full test suite
```

## Scaffold a project

```bash
python -m slowfw new my-service
cd my-service
pip install -r requirements.txt
python -m slowfw run main:app --reload
```

The generated project has a health endpoint, a non-root Dockerfile, a `.env`
example, and a test that runs on both protocols.

"""A typed CRUD service.

Shows the FastAPI half of SlowAPI: dataclass DTOs, validated parameters,
dependency injection, structured errors, and an OpenAPI document generated
from the same annotations that enforce the rules at runtime.

    python -m slowapi run main:app --reload
    open http://localhost:8000/docs
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone

from slowapi import Depends, Header, NotFound, Query, SlowAPI, expose, hidden
from slowapi.middleware import (
    AccessLogMiddleware,
    CORSMiddleware,
    GZipMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

# --------------------------------------------------------------------- models


@dataclass
class Task:
    """A task as it is stored."""

    id: int
    title: str
    done: bool = False
    # Never serialised: internal even though it lives on the model.
    internal_note: str = field(default="", metadata=hidden())
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
        metadata=expose(alias="createdAt"),
    )


@dataclass
class CreateTask:
    """The accepted shape of a create request."""

    title: str
    done: bool = False


@dataclass
class UpdateTask:
    title: str | None = None
    done: bool | None = None


@dataclass
class Page:
    items: list[Task]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------- storage


class TaskStore:
    """An in-memory store. Swap for a database without touching handlers."""

    def __init__(self) -> None:
        self._rows: dict[int, Task] = {}
        self._ids = itertools.count(1)
        for title in ("Read the guide", "Deploy on WSGI", "Deploy on ASGI"):
            self.create(CreateTask(title=title))

    def list(self, *, done: bool | None, limit: int, offset: int) -> tuple[list[Task], int]:
        rows = list(self._rows.values())
        if done is not None:
            rows = [r for r in rows if r.done is done]
        return rows[offset : offset + limit], len(rows)

    def get(self, task_id: int) -> Task:
        try:
            return self._rows[task_id]
        except KeyError:
            raise NotFound(f"No task with id {task_id}") from None

    def create(self, payload: CreateTask) -> Task:
        task = Task(id=next(self._ids), title=payload.title, done=payload.done)
        self._rows[task.id] = task
        return task

    def update(self, task_id: int, payload: UpdateTask) -> Task:
        task = self.get(task_id)
        if payload.title is not None:
            task.title = payload.title
        if payload.done is not None:
            task.done = payload.done
        return task

    def delete(self, task_id: int) -> None:
        self.get(task_id)
        del self._rows[task_id]


store = TaskStore()


# --------------------------------------------------------------- application

app = SlowAPI(
    title="Task API",
    version="1.0.0",
    description="A worked example of SlowAPI's typed, documented request handling.",
)
app.use(
    SecurityHeadersMiddleware(),
    AccessLogMiddleware(),
    CORSMiddleware(allow_origins=["*"]),
    GZipMiddleware(),
    RateLimitMiddleware(limit=300, window=60),
)


# ------------------------------------------------------------- dependencies


@dataclass
class Pagination:
    limit: int
    offset: int


def paginate(
    limit: int = Query(20, ge=1, le=100, description="Rows per page."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> Pagination:
    """A dependency: reusable, validated, and documented once."""
    return Pagination(limit=limit, offset=offset)


def get_store() -> TaskStore:
    return store


# ------------------------------------------------------------------- routes


@app.get("/health", tags=["ops"], include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/tasks", tags=["tasks"], summary="List tasks")
def list_tasks(
    paging: Pagination = Depends(paginate),
    done: bool | None = Query(None, description="Filter by completion."),
    tasks: TaskStore = Depends(get_store),
) -> Page:
    items, total = tasks.list(done=done, limit=paging.limit, offset=paging.offset)
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@app.get("/tasks/{task_id:int}", tags=["tasks"], summary="Fetch one task")
def get_task(task_id: int, tasks: TaskStore = Depends(get_store)) -> Task:
    return tasks.get(task_id)


@app.post("/tasks", tags=["tasks"], status_code=201, summary="Create a task")
def create_task(payload: CreateTask, tasks: TaskStore = Depends(get_store)) -> Task:
    return tasks.create(payload)


@app.patch("/tasks/{task_id:int}", tags=["tasks"], summary="Update a task")
def update_task(task_id: int, payload: UpdateTask, tasks: TaskStore = Depends(get_store)) -> Task:
    return tasks.update(task_id, payload)


@app.delete("/tasks/{task_id:int}", tags=["tasks"], status_code=204, summary="Delete a task")
def delete_task(task_id: int, res, tasks: TaskStore = Depends(get_store)) -> None:
    tasks.delete(task_id)
    res.status(204).end()


@app.get("/whoami", tags=["ops"])
def whoami(
    req,
    user_agent: str = Header("unknown"),
    x_request_id: str | None = Header(None),
) -> dict:
    """Headers are parameters too, with the same coercion and documentation."""
    return {"ip": req.ip, "userAgent": user_agent, "requestId": x_request_id}


if __name__ == "__main__":
    app.run(port=8000)

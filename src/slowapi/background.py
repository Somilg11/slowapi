"""Work that runs after the response has been sent.

Declare a :class:`BackgroundTasks` parameter and the framework hands you one::

    @app.post("/signup")
    def signup(body: SignupDTO, tasks: BackgroundTasks, res: Response):
        user = users.create(body)
        tasks.add(send_welcome_email, user.email)
        return res.status(201).json(user)

The client gets its 201 immediately; the email goes out afterwards.  Tasks run
on both protocols at the same point in the lifecycle -- ASGI runs them once the
final body message is sent, WSGI once the server closes the response iterable.

Sync tasks on a fully synchronous route run inline, so a background task is not
by itself a reason for the loop-free fast path to give up its advantage.  An
``async def`` task hops onto the shared loop, and only then.
"""

from __future__ import annotations

import typing as t

from .concurrency import call_maybe_sync, maybe_await

__all__ = ["BackgroundTasks"]


class _Task(t.NamedTuple):
    func: t.Callable[..., t.Any]
    args: tuple[t.Any, ...]
    kwargs: dict[str, t.Any]


class BackgroundTasks:
    """An ordered list of callables to run after the response.

    Also usable directly as ``response.background``, which is what the adapters
    invoke; an empty instance is a cheap no-op.
    """

    __slots__ = ("_tasks",)

    def __init__(self, tasks: t.Iterable[t.Callable[[], t.Any]] = ()) -> None:
        self._tasks: list[_Task] = [_Task(fn, (), {}) for fn in tasks]

    def add(self, func: t.Callable[..., t.Any], *args: t.Any, **kwargs: t.Any) -> None:
        """Queue ``func(*args, **kwargs)``.  Sync or ``async def``, either works."""
        if not callable(func):
            raise TypeError(f"BackgroundTasks.add() needs a callable, got {type(func).__name__}")
        self._tasks.append(_Task(func, args, kwargs))

    #: Alias for people arriving from Starlette.
    add_task = add

    def __len__(self) -> int:
        return len(self._tasks)

    def __bool__(self) -> bool:
        return bool(self._tasks)

    def __repr__(self) -> str:
        return f"<BackgroundTasks {len(self._tasks)} queued>"

    async def __call__(self) -> None:
        """Run every queued task in order.  The ASGI entry point."""
        for task in self._tasks:
            try:
                await maybe_await(task.func(*task.args, **task.kwargs))
            except Exception:
                self._report(task)

    def run_sync(self) -> None:
        """Run every queued task from synchronous code.  The WSGI entry point.

        Sync tasks run inline in the calling thread.  That matters: a plain
        ``def`` background task must not be the thing that drags a loop-free
        request onto the event loop after all the work spent keeping it off.
        Only an ``async def`` task pays for the hop.
        """
        for task in self._tasks:
            try:
                call_maybe_sync(task.func, *task.args, **task.kwargs)
            except Exception:
                self._report(task)

    @staticmethod
    def _report(task: _Task) -> None:
        """One failing task must not swallow the ones queued behind it.

        The response has already gone out, so there is nobody left to return an
        error to; logging it is the only honest option.
        """
        from .logging import get_logger

        get_logger("slowapi.background").exception(
            "background task failed",
            extra={"task": getattr(task.func, "__qualname__", repr(task.func))},
        )

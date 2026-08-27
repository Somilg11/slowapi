"""A small autoescaping template engine, with a Jinja2 escape hatch.

The built-in engine exists so that ``res.render("index", {...})`` works with no
dependencies at all.  It covers what server-rendered pages actually need --
interpolation, conditionals, loops, includes, inheritance, and filters -- and
autoescapes by default, which the naive ``str.replace`` approach most toy
frameworks ship does not.

If :mod:`jinja2` is installed, pass ``engine="jinja2"`` and every feature of
Jinja is available instead; the ``render`` call site does not change.

Syntax::

    {{ user.name }}            interpolation, HTML-escaped
    {{ user.bio | safe }}      opt out of escaping
    {{ price | round(2) }}     filters, chainable
    {% if user %} ... {% else %} ... {% endif %}
    {% for item in items %} {{ loop.index }} {{ item }} {% endfor %}
    {% include "partial" %}
    {% extends "base" %} {% block body %} ... {% endblock %}
    {# a comment #}
"""

from __future__ import annotations

import html
import os
import re
import typing as t
from dataclasses import dataclass, field

from .exceptions import ConfigurationError

__all__ = ["DEFAULT_FILTERS", "Markup", "TemplateEngine", "TemplateError"]


class TemplateError(ConfigurationError):
    """Raised for syntax errors and missing templates."""


class Markup(str):
    """A string that is already safe HTML and must not be escaped again."""

    __slots__ = ()

    def __html__(self) -> str:
        return str(self)


def escape(value: t.Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "__html__"):
        return value.__html__()
    return html.escape(str(value), quote=True)


DEFAULT_FILTERS: dict[str, t.Callable[..., t.Any]] = {
    "safe": Markup,
    "escape": escape,
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "title": lambda v: str(v).title(),
    "capitalize": lambda v: str(v).capitalize(),
    "trim": lambda v: str(v).strip(),
    "length": len,
    "default": lambda v, fallback="": fallback if v in (None, "") else v,
    "join": lambda v, sep=", ": sep.join(str(x) for x in v),
    "round": lambda v, digits=0: round(float(v), int(digits)),
    "abs": abs,
    "first": lambda v: next(iter(v), ""),
    "last": lambda v: list(v)[-1] if v else "",
    "reverse": lambda v: list(reversed(list(v))),
    "sort": lambda v: sorted(v),
    "tojson": lambda v: Markup(__import__("json").dumps(v).replace("<", "\\u003c")),
}

_TOKEN_RE = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\})", re.DOTALL)
_FOR_RE = re.compile(r"^for\s+(?P<vars>[\w\s,]+)\s+in\s+(?P<iter>.+)$", re.DOTALL)


@dataclass
class _Node:
    kind: str
    #: Literal text, or the expression/condition source.
    payload: str = ""
    children: list[_Node] = field(default_factory=list)
    #: Used by ``if`` (elif/else branches) and ``block``.
    alternates: list[tuple[str, list[_Node]]] = field(default_factory=list)
    loop_vars: tuple[str, ...] = ()


class _AttrDict(dict):
    """A dict whose keys are also reachable as attributes.

    Templates read ``{{ user.name }}`` whether ``user`` is an object or a row
    from a database driver, so mappings placed in the render scope are wrapped
    here rather than forcing every caller to convert their data first.
    """

    __slots__ = ()

    def __getattr__(self, key: str) -> t.Any:
        try:
            return _wrap(self[key])
        except KeyError:
            raise AttributeError(key) from None

    def __getitem__(self, key: t.Any) -> t.Any:
        return _wrap(dict.__getitem__(self, key))


def _wrap(value: t.Any) -> t.Any:
    """Make mappings and sequences of mappings attribute-friendly."""
    if isinstance(value, _AttrDict):
        return value
    if isinstance(value, dict):
        return _AttrDict(value)
    if isinstance(value, (list, tuple)):
        return [_wrap(item) for item in value]
    return value


class _Loop:
    """The ``loop`` object exposed inside ``{% for %}`` bodies."""

    __slots__ = ("index0", "length")

    def __init__(self, index0: int, length: int) -> None:
        self.index0 = index0
        self.length = length

    @property
    def index(self) -> int:
        return self.index0 + 1

    @property
    def first(self) -> bool:
        return self.index0 == 0

    @property
    def last(self) -> bool:
        return self.index0 == self.length - 1

    @property
    def even(self) -> bool:
        return self.index0 % 2 == 1

    @property
    def odd(self) -> bool:
        return self.index0 % 2 == 0


class TemplateEngine:
    """Loads, parses, caches and renders templates from a directory."""

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        extension: str = ".html",
        autoescape: bool = True,
        cache: bool = True,
        engine: str = "builtin",
        filters: t.Mapping[str, t.Callable[..., t.Any]] | None = None,
        globals: t.Mapping[str, t.Any] | None = None,
    ) -> None:
        self.directory = os.path.abspath(os.fspath(directory))
        self.extension = extension
        self.autoescape = autoescape
        self.cache_enabled = cache
        self.filters = {**DEFAULT_FILTERS, **(filters or {})}
        self.globals = dict(globals or {})
        self._cache: dict[str, list[_Node]] = {}
        self._jinja: t.Any = None

        if engine == "jinja2":
            try:
                from jinja2 import Environment, FileSystemLoader, select_autoescape
            except ImportError as exc:  # pragma: no cover - optional path
                raise ConfigurationError(
                    "engine='jinja2' requires jinja2. Install it with "
                    "`pip install slowapi[templates]`."
                ) from exc
            self._jinja = Environment(
                loader=FileSystemLoader(self.directory),
                autoescape=select_autoescape(default=autoescape),
                auto_reload=not cache,
            )
            self._jinja.filters.update(filters or {})
            self._jinja.globals.update(self.globals)
        elif engine != "builtin":
            raise ConfigurationError(f"Unknown template engine {engine!r}")

    # ------------------------------------------------------------- loading

    def _resolve(self, name: str) -> str:
        """Map a template name to a path, refusing to escape the root."""
        filename = name if os.path.splitext(name)[1] else name + self.extension
        path = os.path.abspath(os.path.join(self.directory, filename))
        if os.path.commonpath([path, self.directory]) != self.directory:
            raise TemplateError(f"Template name escapes the template root: {name!r}")
        if not os.path.isfile(path):
            raise TemplateError(f"Template not found: {path}")
        return path

    def _load(self, name: str) -> list[_Node]:
        if self.cache_enabled and name in self._cache:
            return self._cache[name]
        with open(self._resolve(name), encoding="utf-8") as fp:
            nodes = self._parse(fp.read(), name)
        if self.cache_enabled:
            self._cache[name] = nodes
        return nodes

    # ------------------------------------------------------------- parsing

    def _parse(self, source: str, name: str) -> list[_Node]:
        tokens = _TOKEN_RE.split(source)
        root: list[_Node] = []
        stack: list[_Node] = []

        def emit(node: _Node) -> None:
            if not stack:
                root.append(node)
                return
            top = stack[-1]
            # Once an {% elif %}/{% else %} has been seen, content belongs to
            # that branch rather than to the if-block's main body.
            if top.kind == "if" and top.alternates:
                top.alternates[-1][1].append(node)
            else:
                top.children.append(node)

        for token in tokens:
            if not token:
                continue
            if token.startswith("{#"):
                continue
            if token.startswith("{{"):
                emit(_Node("expr", token[2:-2].strip()))
                continue
            if not token.startswith("{%"):
                emit(_Node("text", token))
                continue

            statement = token[2:-2].strip()
            keyword = statement.split(None, 1)[0] if statement else ""

            if keyword == "if":
                node = _Node("if", statement[2:].strip())
                emit(node)
                stack.append(node)
            elif keyword in ("elif", "else"):
                if not stack or stack[-1].kind != "if":
                    raise TemplateError(f"{keyword!r} outside an if block in {name!r}")
                condition = statement[4:].strip() if keyword == "elif" else "True"
                stack[-1].alternates.append((condition, []))
            elif keyword == "endif":
                _expect(stack, "if", name)
            elif keyword == "for":
                match = _FOR_RE.match(statement)
                if match is None:
                    raise TemplateError(f"Malformed for loop in {name!r}: {statement!r}")
                node = _Node("for", match.group("iter").strip())
                node.loop_vars = tuple(v.strip() for v in match.group("vars").split(","))
                emit(node)
                stack.append(node)
            elif keyword == "endfor":
                _expect(stack, "for", name)
            elif keyword == "block":
                node = _Node("block", statement[5:].strip())
                emit(node)
                stack.append(node)
            elif keyword == "endblock":
                _expect(stack, "block", name)
            elif keyword == "include":
                emit(_Node("include", _unquote(statement[7:].strip())))
            elif keyword == "extends":
                emit(_Node("extends", _unquote(statement[7:].strip())))
            elif keyword == "set":
                emit(_Node("set", statement[3:].strip()))
            else:
                raise TemplateError(f"Unknown tag {{% {keyword} %}} in {name!r}")

        if stack:
            raise TemplateError(f"Unclosed {{% {stack[-1].kind} %}} in {name!r}")
        return root

    # ------------------------------------------------------------ rendering

    def render(self, name: str, context: t.Mapping[str, t.Any] | None = None) -> str:
        """Render ``name`` with ``context`` and return the markup."""
        if self._jinja is not None:  # pragma: no cover - optional path
            filename = name if os.path.splitext(name)[1] else name + self.extension
            return self._jinja.get_template(filename).render(**(context or {}))

        scope: dict[str, t.Any] = {**self.globals, **(context or {})}
        nodes = self._load(name)

        parent = next((n for n in nodes if n.kind == "extends"), None)
        if parent is not None:
            blocks = {n.payload: n.children for n in nodes if n.kind == "block"}
            return self._render_nodes(self._load(parent.payload), scope, blocks)
        return self._render_nodes(nodes, scope, {})

    def render_string(self, source: str, context: t.Mapping[str, t.Any] | None = None) -> str:
        """Render a template held in memory.  Handy for tests and emails."""
        nodes = self._parse(source, "<string>")
        return self._render_nodes(nodes, {**self.globals, **(context or {})}, {})

    def _render_nodes(
        self,
        nodes: t.Sequence[_Node],
        scope: dict[str, t.Any],
        blocks: dict[str, list[_Node]],
    ) -> str:
        out: list[str] = []
        for node in nodes:
            if node.kind == "text":
                out.append(node.payload)
            elif node.kind == "expr":
                out.append(self._render_expr(node.payload, scope))
            elif node.kind == "set":
                target, _, expression = node.payload.partition("=")
                scope[target.strip()] = self._eval(expression.strip(), scope)
            elif node.kind == "if":
                out.append(self._render_if(node, scope, blocks))
            elif node.kind == "for":
                out.append(self._render_for(node, scope, blocks))
            elif node.kind == "include":
                out.append(self._render_nodes(self._load(node.payload), scope, blocks))
            elif node.kind == "block":
                body = blocks.get(node.payload, node.children)
                out.append(self._render_nodes(body, scope, blocks))
            elif node.kind == "extends":
                continue
        return "".join(out)

    def _render_if(
        self, node: _Node, scope: dict[str, t.Any], blocks: dict[str, list[_Node]]
    ) -> str:
        if self._eval(node.payload, scope):
            return self._render_nodes(node.children, scope, blocks)
        for condition, body in node.alternates:
            if self._eval(condition, scope):
                return self._render_nodes(body, scope, blocks)
        return ""

    def _render_for(
        self, node: _Node, scope: dict[str, t.Any], blocks: dict[str, list[_Node]]
    ) -> str:
        iterable = list(self._eval(node.payload, scope) or [])
        out: list[str] = []
        for index, item in enumerate(iterable):
            local = dict(scope)
            if len(node.loop_vars) == 1:
                local[node.loop_vars[0]] = item
            else:
                for var, value in zip(node.loop_vars, item, strict=False):
                    local[var] = value
            local["loop"] = _Loop(index, len(iterable))
            out.append(self._render_nodes(node.children, local, blocks))
        return "".join(out)

    def _render_expr(self, source: str, scope: dict[str, t.Any]) -> str:
        expression, *filters = _split_filters(source)
        value = self._eval(expression, scope)
        explicitly_safe = False
        for spec in filters:
            name, args = _parse_filter(spec)
            fn = self.filters.get(name)
            if fn is None:
                raise TemplateError(f"Unknown filter {name!r}")
            value = fn(value, *[self._eval(a, scope) for a in args])
            explicitly_safe = explicitly_safe or name == "safe"
        if self.autoescape and not explicitly_safe:
            return escape(value)
        return "" if value is None else str(value)

    def _eval(self, source: str, scope: t.Mapping[str, t.Any]) -> t.Any:
        """Evaluate a template expression against ``scope``.

        Templates are authored by the application, not by end users, so
        expression evaluation is intentionally permissive; the ``__builtins__``
        stripping below is a guard against typos reaching dangerous names, not
        a sandbox.  Never render a template whose source came from a request.
        """
        if not source:
            return ""
        try:
            local = {k: _wrap(v) for k, v in scope.items()}
            return eval(source, {"__builtins__": _SAFE_BUILTINS}, local)
        except NameError:
            return ""
        except Exception as exc:
            raise TemplateError(f"Error evaluating {source!r}: {exc}") from exc


_SAFE_BUILTINS = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "sorted": sorted,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "enumerate": enumerate,
    "zip": zip,
    "range": range,
    "reversed": reversed,
    "any": any,
    "all": all,
    "True": True,
    "False": False,
    "None": None,
}


def _expect(stack: list[_Node], kind: str, name: str) -> None:
    if not stack or stack[-1].kind != kind:
        raise TemplateError(f"Unexpected {{% end{kind} %}} in {name!r}")
    stack.pop()


def _unquote(value: str) -> str:
    return value.strip().strip("\"'")


def _split_filters(source: str) -> list[str]:
    """Split ``a.b | filter(1) | other`` on pipes outside brackets/strings."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in source:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_filter(spec: str) -> tuple[str, list[str]]:
    if "(" not in spec:
        return spec.strip(), []
    name, _, rest = spec.partition("(")
    args = rest.rstrip().rstrip(")")
    return name.strip(), [a.strip() for a in args.split(",") if a.strip()]

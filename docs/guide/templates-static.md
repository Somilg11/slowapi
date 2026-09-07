# Templates and static files

Not every application is a JSON API. SlowAPI ships a small autoescaping
template engine so that server-rendered pages work with no dependencies at
all, and gets out of the way if you want Jinja2.

## Enabling templates

```python
app = SlowAPI(template_dir="templates")
# or later
app.configure_templates("templates", engine="jinja2")
```

```python
@app.get("/")
def home(req, res):
    res.render("index", {"user": req.state.user, "posts": posts})
```

`res.render(name, context)` looks for `templates/name.html`. Templates are
cached unless `debug=True`.

## Syntax

```html
{{ user.name }}                     <!-- interpolated, HTML-escaped -->
{{ post.body | safe }}              <!-- opt out of escaping, deliberately -->
{{ price | round(2) }}              <!-- filters, chainable -->

{% if user %}Hi {{ user.name }}{% elif guest %}Hi{% else %}Sign in{% endif %}

{% for post in posts %}
  <li>{{ loop.index }}. {{ post.title }}{% if not loop.last %},{% endif %}</li>
{% endfor %}

{% for key, value in items %}{{ key }}={{ value }}{% endfor %}

{% set total = len(posts) %}
{% include "partial/footer" %}
{# a comment, not rendered #}
```

`loop` exposes `index`, `index0`, `first`, `last`, `even`, `odd`, `length`.

Dictionaries support dot access, so `{{ user.name }}` works whether `user` is
an object or a row from a database driver.

## Inheritance

```html
<!-- templates/base.html -->
<!doctype html>
<html>
  <body>
    <header>{% block header %}{% endblock %}</header>
    <main>{% block content %}{% endblock %}</main>
  </body>
</html>
```

```html
<!-- templates/index.html -->
{% extends "base" %}
{% block content %}<h1>{{ title }}</h1>{% endblock %}
```

## Escaping

Interpolation escapes `<`, `>`, `&`, `"` and `'` by default. This is the whole
reason not to build HTML with `str.replace`:

```python
res.render("index", {"title": "<script>alert(1)</script>"})
```

renders as text, not as a script. Opting out requires writing `| safe`, which
is greppable in review.

Values that are already safe HTML can say so:

```python
from slowfw.templating import Markup

res.render("post", {"body": Markup(markdown_to_html(post.body))})
```

## Filters

Built in: `safe`, `escape`, `upper`, `lower`, `title`, `capitalize`, `trim`,
`length`, `default(x)`, `join(sep)`, `round(n)`, `abs`, `first`, `last`,
`reverse`, `sort`, `tojson`.

Add your own:

```python
app.configure_templates(
    "templates",
    filters={"currency": lambda v: f"£{v / 100:,.2f}"},
    globals={"site_name": "Example"},
)
```

## Jinja2

```python
app.configure_templates("templates", engine="jinja2")
```

Every Jinja feature becomes available — macros, `{% extends %}` chains,
`{% with %}`, the full filter library — and `res.render(...)` does not change.
Requires `pip install "slowfw[templates]"`.

Use the built-in engine when you want zero dependencies and the features above
are enough. Use Jinja2 when you want macros, complex inheritance, or an
existing template library.

## Security note

Templates are evaluated with Python expression semantics. They are authored by
your application, not by end users. **Never render a template whose source came
from a request** — that is remote code execution, in any engine.

Rendering *user data* through a template is exactly what the escaping is for
and is entirely safe.

## Static files

```python
app.mount_static("/assets", "public", max_age=31_536_000, immutable=True)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `index_file` | `"index.html"` | Served for directory requests |
| `max_age` | `3600` | `Cache-Control` seconds |
| `immutable` | `False` | Adds `immutable` — for content-hashed filenames |
| `follow_symlinks` | `False` | Refuse symlinks by default |
| `html_fallback` | `None` | Serve this file for unmatched paths (SPA routing) |

What you get:

- **ETag and Last-Modified**, from the file's inode metadata.
- **Conditional requests**: a matching `If-None-Match` gets a `304`.
- **Byte ranges**, so `<video>` seeking and resumable downloads work.
- **Traversal protection**: every candidate path is resolved and checked
  against the root, so `..`, URL-encoded `..`, and symlinks pointing outside
  the directory are all refused.

### Single-page applications

```python
app.mount_static("/", "dist", html_fallback="index.html")
```

Unmatched paths serve `index.html`, letting the client router handle them.
Register your API routes first — the router prefers literal segments, so
`/api/...` still wins.

### In production

Serving static files from Python works and is fine at low volume. At scale, put
them behind a CDN or let nginx serve the directory directly; the framework is
not trying to be a file server.

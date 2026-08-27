# fullstack

Server-rendered pages, form posts, flash messages, sessions, static files.

```bash
export SECRET_KEY=$(python -m slowapi secret)
python -m slowapi run main:app --reload
open http://localhost:8000
```

## What to notice

- **`{{ note.body }}` is escaped.** Paste `<script>alert(1)</script>` into the
  form and read the page source; it is text, not a script. Opting out takes a
  deliberate `| safe`.
- **Templates inherit.** `index.html` fills a block in `base.html`; there is no
  copy of the layout.
- **The session is a signed cookie.** No Redis, no table, no sticky sessions.
  Tamper with the cookie in devtools and the server treats it as absent.
- **POST then redirect.** `303` after a successful write, so a refresh does not
  resubmit the form.
- **Static files get ETags and support range requests.** A second visit is a
  `304`.

The whole thing has no runtime dependency beyond SlowAPI itself. If you want
Jinja2 instead of the built-in engine, change one argument:

```python
app.configure_templates("templates", engine="jinja2")
```

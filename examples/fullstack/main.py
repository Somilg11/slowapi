"""A server-rendered application: templates, sessions, forms, static files.

No JavaScript build step, no API client, no JSON. This is the other half of
what a web framework is for, and it is the half most modern Python frameworks
quietly stopped supporting well.

    python -m slowapi run main:app --reload
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone

from slowapi import Form, SlowAPI
from slowapi.config import Settings
from slowapi.middleware import SecurityHeadersMiddleware, SessionMiddleware

settings = Settings.load()

app = SlowAPI(
    title="Notes",
    version="1.0.0",
    template_dir="templates",
    # An HTML app loads its own CSS, so the API-grade default policy is relaxed
    # exactly as far as needed and no further.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount_static("/static", "static", max_age=3600)
app.use(
    SecurityHeadersMiddleware(
        content_security_policy="default-src 'self'; style-src 'self'; frame-ancestors 'none'"
    ),
    SessionMiddleware(settings.require_secret(), secure=settings.is_production),
)


@dataclass
class Note:
    id: int
    body: str
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


NOTES: dict[int, Note] = {}
IDS = itertools.count(1)


@app.get("/", name="home")
def home(req, res):
    """Render a template. Everything interpolated is HTML-escaped by default."""
    session = req.state.session
    res.render(
        "index",
        {
            "notes": sorted(NOTES.values(), key=lambda n: n.id, reverse=True),
            "flashes": session.get_flashes(),
            "count": len(NOTES),
        },
    )


@app.post("/notes")
def create_note(req, res, body: str = Form(...)):
    """Handle a classic form post, then redirect so refresh does not resubmit."""
    text = body.strip()
    if not text:
        req.state.session.flash("A note cannot be empty.", "error")
    else:
        note = Note(id=next(IDS), body=text)
        NOTES[note.id] = note
        req.state.session.flash("Note saved.", "success")
    res.redirect(app.url_for("home"), 303)


@app.post("/notes/{note_id:int}/delete")
def delete_note(req, res, note_id: int):
    if NOTES.pop(note_id, None) is not None:
        req.state.session.flash("Note deleted.", "success")
    res.redirect(app.url_for("home"), 303)


if __name__ == "__main__":
    app.run(port=settings.port)

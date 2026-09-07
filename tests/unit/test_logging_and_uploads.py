"""Structured logging, and multipart uploads.

Logging is what you have left when a production request goes wrong, so the
JSON shape is pinned here: a formatter that drops the request id, or crashes on
a value it cannot serialise, takes the incident report with it.
"""

from __future__ import annotations

import dataclasses
import io
import json
import logging

from slowfw import Request, Response, SlowAPI, UploadFile
from slowfw.logging import JSONFormatter, configure_logging, get_logger


@dataclasses.dataclass
class Payload:
    """Module level: ``get_type_hints`` cannot resolve a method-local class
    once ``from __future__ import annotations`` turns annotations to strings."""

    name: str


class TestJSONFormatter:
    def _emit(self, record_factory, **kwargs):
        formatter = JSONFormatter(**kwargs)
        return json.loads(formatter.format(record_factory()))

    def _record(self, **extra):
        record = logging.LogRecord(
            name="slowfw.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="something happened",
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_the_core_fields_are_present(self):
        payload = self._emit(self._record)
        assert payload["event"] == "something happened"
        assert payload["level"] == "info"
        assert payload["logger"] == "slowfw.test"
        assert "ts" in payload

    def test_service_and_version_are_stamped_when_configured(self):
        """So one aggregated stream can be split by deployment."""
        payload = self._emit(self._record, service="billing", version="2.1.0")
        assert payload["service"] == "billing" and payload["version"] == "2.1.0"

    def test_extras_are_merged_into_the_document(self):
        """A log line you cannot filter on is a log line you cannot use."""
        payload = self._emit(lambda: self._record(status=404, path="/x"))
        assert payload["status"] == 404 and payload["path"] == "/x"

    def test_an_unserialisable_extra_does_not_lose_the_line(self):
        """A log line dropped because one field would not encode is the worst
        possible moment to lose a log line."""
        payload = self._emit(lambda: self._record(thing=object()))
        assert payload["event"] == "something happened"
        assert isinstance(payload["thing"], str)

    def test_an_exception_is_rendered_as_text(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            import sys

            record = self._record()
            record.exc_info = sys.exc_info()
            payload = json.loads(JSONFormatter().format(record))

        assert "ValueError" in payload["exception"]
        assert "kaboom" in payload["exception"]

    def test_the_output_is_one_line(self):
        """Multi-line JSON breaks every line-oriented log shipper."""
        formatted = JSONFormatter().format(self._record(detail="a\nb"))
        assert "\n" not in formatted


class TestConfiguration:
    def test_a_level_can_be_set_by_name(self):
        configure_logging("WARNING")
        assert get_logger("slowfw.x").getEffectiveLevel() >= logging.WARNING
        configure_logging("CRITICAL")  # restore the quiet default for the suite

    def test_json_mode_produces_parseable_output(self):
        stream = io.StringIO()
        configure_logging("INFO", json_output=True, stream=stream)
        get_logger("slowfw.test").info("hello", extra={"k": "v"})

        payload = json.loads(stream.getvalue().strip().splitlines()[-1])

        assert payload["event"] == "hello" and payload["k"] == "v"
        configure_logging("CRITICAL")

    def test_calling_it_twice_does_not_duplicate_output(self):
        """Auto-reload calls it on every restart."""
        stream = io.StringIO()
        configure_logging("INFO", json_output=True, stream=stream)
        configure_logging("INFO", json_output=True, stream=stream)
        get_logger("slowfw.test").info("once")

        assert len(stream.getvalue().strip().splitlines()) == 1
        configure_logging("CRITICAL")


class TestAccessLog:
    def test_the_status_reported_is_the_status_sent(self, make_client, caplog):
        """A deliberate 404 logged as 500 sends people hunting a bug that is
        not there."""
        app = SlowAPI()

        with caplog.at_level(logging.INFO, logger="slowfw.access"):
            make_client(app).get("/nope")

        statuses = [getattr(r, "status", None) for r in caplog.records]
        assert 500 not in statuses

    def test_a_request_id_is_attached_and_returned(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(req, res: Response):
            return res.json({"id": req.request_id})

        response = make_client(app).get("/x")
        assert response.json()["id"]
        assert response.headers.get("x-request-id") == response.json()["id"]

    def test_an_inbound_request_id_is_preserved(self, make_client):
        """Otherwise a trace stops at this service's front door."""
        app = SlowAPI()

        @app.get("/x")
        def handler(req, res: Response):
            return res.json({"id": req.request_id})

        response = make_client(app).get("/x", headers={"x-request-id": "trace-123"})
        assert response.json()["id"] == "trace-123"


class TestFileUploads:
    def _upload_app(self):
        app = SlowAPI()

        @app.post("/upload")
        def upload(avatar: UploadFile, res: Response):
            from slowfw.concurrency import call_maybe_sync

            content = call_maybe_sync(avatar.read)
            return res.json(
                {
                    "filename": avatar.filename,
                    "content_type": avatar.content_type,
                    "size": len(content),
                    "text": content.decode(),
                }
            )

        return app

    def test_a_single_file_arrives_intact(self, make_client):
        client = make_client(self._upload_app())

        response = client.post(
            "/upload", files={"avatar": ("hello.txt", io.BytesIO(b"hi there"), "text/plain")}
        )

        assert response.json() == {
            "filename": "hello.txt",
            "content_type": "text/plain",
            "size": 8,
            "text": "hi there",
        }

    def test_a_missing_required_file_is_a_422(self, make_client):
        response = make_client(self._upload_app()).post("/upload", data={"other": "x"})
        assert response.status_code == 422

    def test_form_fields_and_files_arrive_together(self, make_client):
        app = SlowAPI()

        @app.post("/upload")
        def upload(res: Response, avatar: UploadFile, caption: str = ""):
            return res.json({"caption": caption, "filename": avatar.filename})

        response = make_client(app).post(
            "/upload",
            data={"caption": "my face"},
            files={"avatar": ("a.png", io.BytesIO(b"\x89PNG"), "image/png")},
        )

        assert response.json() == {"caption": "my face", "filename": "a.png"}

    def test_several_files_under_one_field(self, make_client):
        app = SlowAPI()

        @app.post("/upload")
        def upload(res: Response, docs: list[UploadFile] | None = None):
            return res.json({"names": [d.filename for d in docs]})

        response = make_client(app).post(
            "/upload",
            files=[
                ("docs", ("a.txt", io.BytesIO(b"a"), "text/plain")),
                ("docs", ("b.txt", io.BytesIO(b"b"), "text/plain")),
            ],
        )

        assert response.json() == {"names": ["a.txt", "b.txt"]}

    def test_upload_handles_are_closed_after_the_request(self, make_client):
        """A leaked SpooledTemporaryFile is a file descriptor leak per request."""
        app = SlowAPI()
        captured = []

        @app.post("/upload")
        def upload(res: Response, avatar: UploadFile | None = None):
            captured.append(avatar)
            return res.json({})

        make_client(app).post(
            "/upload", files={"avatar": ("a.txt", io.BytesIO(b"a"), "text/plain")}
        )

        assert captured and captured[0]._file.closed


class TestFormScalars:
    """A scalar beside an upload used to be answered with a JSON parse error."""

    def test_a_field_and_a_file_arrive_together(self, make_client):
        app = SlowAPI()

        @app.post("/upload")
        def upload(res: Response, avatar: UploadFile, caption: str = ""):
            return res.json({"caption": caption, "filename": avatar.filename})

        response = make_client(app).post(
            "/upload",
            data={"caption": "my face"},
            files={"avatar": ("a.png", io.BytesIO(b"\x89PNG"), "image/png")},
        )

        assert response.json() == {"caption": "my face", "filename": "a.png"}

    def test_url_encoded_scalars_resolve_without_json(self, make_client):
        app = SlowAPI()

        @app.post("/login")
        def login(res: Response, user: str = "", remember: bool = False):
            return res.json({"user": user, "remember": remember})

        response = make_client(app).post(
            "/login",
            data="user=ada&remember=true",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        assert response.json() == {"user": "ada", "remember": True}

    def test_a_missing_form_field_still_falls_back_to_the_query_string(self, make_client):
        app = SlowAPI()

        @app.post("/x")
        def handler(res: Response, tag: str = "none"):
            return res.json({"tag": tag})

        response = make_client(app).post(
            "/x?tag=from-query",
            data={"other": "x"},
            files={"f": ("a.txt", io.BytesIO(b"a"), "text/plain")},
        )

        assert response.json() == {"tag": "from-query"}

    def test_json_bodies_are_unaffected(self, make_client):
        app = SlowAPI()

        @app.post("/x")
        def handler(res: Response, name: str = ""):
            return res.json({"name": name})

        assert make_client(app).post("/x", json={"name": "ada"}).json() == {"name": "ada"}


class TestOptionalAnnotations:
    """``T | None`` must classify as ``T``, not fall through to the query string."""

    def test_an_optional_upload_is_still_an_upload(self, make_client):
        app = SlowAPI()

        @app.post("/upload")
        def upload(res: Response, avatar: UploadFile | None = None):
            return res.json({"filename": avatar.filename if avatar else None})

        client = make_client(app)
        sent = client.post("/upload", files={"avatar": ("a.png", io.BytesIO(b"x"), "image/png")})
        assert sent.json() == {"filename": "a.png"}
        assert client.post("/upload", data={}).json() == {"filename": None}

    def test_an_optional_request_object_still_injects(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, req: Request | None = None):
            return res.json({"path": req.path if req else None})

        assert make_client(app).get("/x").json() == {"path": "/x"}

    def test_an_optional_dto_still_reads_the_body(self, make_client):
        app = SlowAPI()

        @app.post("/x")
        def handler(res: Response, payload: Payload | None = None):
            return res.json({"name": payload.name if payload else None})

        assert make_client(app).post("/x", json={"name": "ada"}).json() == {"name": "ada"}

    def test_a_plain_optional_scalar_is_unaffected(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, q: str | None = None):
            return res.json({"q": q})

        client = make_client(app)
        assert client.get("/x").json() == {"q": None}
        assert client.get("/x?q=hi").json() == {"q": "hi"}

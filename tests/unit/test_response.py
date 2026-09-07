"""Response construction, headers, cookies, and file serving."""

from __future__ import annotations

import json

import pytest

from slowfw.exceptions import ConfigurationError
from slowfw.response import FileResponse, JSONResponse, Response


class TestExpressAPI:
    def test_mutators_chain(self):
        response = Response().status(201).set("X-A", "1").json({"ok": True})
        assert response.status_code == 201
        assert response.headers["x-a"] == "1"
        assert json.loads(response.body) == {"ok": True}

    def test_send_picks_json_for_mappings_and_html_for_text(self):
        assert Response().send({"a": 1}).headers["content-type"].startswith("application/json")
        assert Response().send("hi").headers["content-type"].startswith("text/html")

    def test_content_length_tracks_the_body(self):
        assert Response().text("hello").headers["content-length"] == "5"

    def test_bodyless_statuses_drop_the_body_and_its_headers(self):
        response = Response().text("ignored").status(204)
        response._finalise_headers()
        assert response.body == b""
        assert "content-length" not in response.headers

    def test_type_expands_bare_extensions(self):
        assert Response().type("json").headers["content-type"].startswith("application/json")

    def test_vary_deduplicates(self):
        response = Response().vary("Origin").vary("origin", "Accept-Encoding")
        assert response.headers["vary"] == "accept-encoding, origin"


class TestCookies:
    def test_defaults_are_the_secure_ones(self):
        header = Response().cookie("sid", "abc").headers["set-cookie"]
        assert "HttpOnly" in header
        assert "SameSite=lax" in header

    def test_samesite_none_without_secure_is_refused(self):
        with pytest.raises(ConfigurationError, match="requires secure"):
            Response().cookie("sid", "x", samesite="none")

    def test_multiple_cookies_are_separate_headers(self):
        response = Response().cookie("a", "1").cookie("b", "2")
        assert len(response.headers.getlist("set-cookie")) == 2

    def test_clear_cookie_expires_it(self):
        assert "Max-Age=0" in Response().clear_cookie("sid").headers["set-cookie"]


class TestJSONResponse:
    def test_dataclasses_and_dates_serialise(self):
        from dataclasses import dataclass
        from datetime import date

        @dataclass
        class Row:
            when: date

        assert json.loads(JSONResponse(Row(date(2024, 1, 2))).body) == {"when": "2024-01-02"}

    def test_unserialisable_objects_raise_rather_than_stringify(self):
        with pytest.raises(TypeError, match="not JSON serialisable"):
            JSONResponse(object())


class TestFileResponse:
    @pytest.fixture
    def sample(self, tmp_path):
        path = tmp_path / "data.txt"
        path.write_bytes(b"0123456789")
        return path

    def test_sets_validators_and_length(self, sample):
        response = FileResponse(sample)
        assert response.headers["content-length"] == "10"
        assert response.headers["accept-ranges"] == "bytes"
        assert "etag" in response.headers
        assert b"".join(response.iter_chunks_sync()) == b"0123456789"

    def test_byte_range_returns_206_and_the_slice(self, sample):
        response = FileResponse(sample, range_header="bytes=2-4")
        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 2-4/10"
        assert b"".join(response.iter_chunks_sync()) == b"234"

    def test_suffix_range_reads_from_the_end(self, sample):
        response = FileResponse(sample, range_header="bytes=-3")
        assert b"".join(response.iter_chunks_sync()) == b"789"

    def test_unsatisfiable_range_is_416(self, sample):
        response = FileResponse(sample, range_header="bytes=99-")
        assert response.status_code == 416

    def test_missing_file_is_a_404_not_an_oserror(self, tmp_path):
        from slowfw.exceptions import NotFound

        with pytest.raises(NotFound):
            FileResponse(tmp_path / "absent")

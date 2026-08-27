"""The awkward parts of HTTP that handlers should not have to think about.

Content negotiation, malformed bodies, oversized uploads, conditional requests,
byte ranges, and header quirks -- each one has a defined answer here so that a
handler can assume a well-formed request.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from slowapi import Response, SlowAPI


@dataclasses.dataclass
class Item:
    name: str
    price: float
    tags: list[str] = dataclasses.field(default_factory=list)


class TestBodyHandling:
    def test_malformed_json_is_a_400_not_a_500(self, make_client):
        app = SlowAPI()

        @app.post("/items")
        def create(item: Item, res: Response):
            return res.json({"ok": True})

        response = make_client(app).post(
            "/items", data=b"{not json", headers={"content-type": "application/json"}
        )
        assert response.status_code == 400

    def test_a_body_over_the_limit_is_refused_with_413(self, make_client):
        """Checked when the body is read, so an ignored body costs nothing."""
        app = SlowAPI(max_body_size=100)

        @app.post("/items")
        def create(req, res: Response):
            from slowapi.concurrency import call_maybe_sync

            return res.json({"size": len(call_maybe_sync(req.body))})

        response = make_client(app).post("/items", data=b"x" * 500)
        assert response.status_code == 413

    def test_an_oversized_body_nobody_reads_is_not_an_error(self, make_client):
        app = SlowAPI(max_body_size=100)

        @app.post("/ping")
        def ping(res: Response):
            return res.json({"ok": True})

        assert make_client(app).post("/ping", data=b"x" * 500).status_code in (200, 201)

    def test_an_empty_body_on_a_post_is_not_a_crash(self, make_client):
        app = SlowAPI()

        @app.post("/items")
        def create(req, res: Response):
            return res.json({"received": None})

        assert make_client(app).post("/items", data=b"").status_code in (200, 201, 422)

    def test_a_dto_is_validated_and_reported_field_by_field(self, make_client):
        app = SlowAPI()

        @app.post("/items")
        def create(item: Item, res: Response):
            return res.json({"name": item.name})

        response = make_client(app).post("/items", json={"price": "free"})

        assert response.status_code == 422
        fields = {tuple(d["loc"])[-1] for d in response.json()["error"]["details"]}
        # Every problem at once, not one per round trip.
        assert {"name", "price"} <= fields

    def test_form_encoded_bodies_are_parsed(self, make_client):
        app = SlowAPI()

        @app.post("/login")
        def login(req, res: Response):
            from slowapi.concurrency import call_maybe_sync

            form = call_maybe_sync(req.form)
            return res.json({"user": form.get("user")})

        response = make_client(app).post(
            "/login",
            data="user=ada&password=x",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.json() == {"user": "ada"}


class TestResponseBuilding:
    def test_the_chain_reads_left_to_right(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.status(201).set("x-custom", "1").json({"ok": True})

        response = make_client(app).get("/x")
        assert response.status_code == 201
        assert response.headers["x-custom"] == "1"
        assert response.json() == {"ok": True}

    def test_content_length_tracks_the_final_body(self, make_client):
        """A stale Content-Length truncates the response or hangs the client."""
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            res.json({"a": "aaaaaaaaaaaaaaaaaaaa"})
            return res.text("short")

        response = make_client(app).get("/x")
        assert response.headers["content-length"] == str(len(b"short"))
        assert response.text == "short"

    def test_204_and_304_carry_no_body(self, make_client):
        app = SlowAPI()

        @app.get("/empty")
        def empty(res: Response):
            return res.status(204).end()

        response = make_client(app).get("/empty")
        assert response.status_code == 204
        assert response.content == b""

    def test_head_returns_the_headers_without_the_body(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({"a": 1})

        response = make_client(app).head("/x")
        assert response.status_code == 200
        assert response.content == b""
        assert "content-type" in response.headers

    def test_a_redirect_sets_location_and_a_3xx(self, make_client):
        app = SlowAPI()

        @app.get("/old")
        def old(res: Response):
            return res.redirect("/new", 301)

        response = make_client(app).get("/old")
        assert response.status_code == 301
        assert response.headers["location"] == "/new"

    def test_cookies_are_set_with_their_attributes(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.cookie("a", "1", max_age=60, httponly=True, samesite="strict").json({})

        header = make_client(app).get("/x").headers["set-cookie"]
        assert (
            "a=1" in header
            and "HttpOnly" in header
            and "SameSite=strict" in header.replace("samesite", "SameSite")
        )

    def test_streaming_sends_every_chunk(self, make_client):
        app = SlowAPI()

        @app.get("/stream")
        def stream(res: Response):
            return res.stream(iter([b"one", b"two", b"three"]))

        assert make_client(app).get("/stream").text == "onetwothree"

    def test_an_etag_enables_a_conditional_request(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        client = make_client(app)
        first = client.get("/x")
        second = client.get("/x", headers={"if-none-match": first.headers["etag"]})

        assert second.status_code == 304

    def test_no_cache_says_so_in_every_dialect(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.no_cache().json({})

        cache_control = make_client(app).get("/x").headers["cache-control"]
        assert "no-store" in cache_control


class TestHeadersAndUrls:
    def test_header_lookup_ignores_case(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(req, res: Response):
            return res.json({"a": req.get("X-CuStOm"), "b": req.headers.get("x-custom")})

        assert make_client(app).get("/x", headers={"x-custom": "v"}).json() == {"a": "v", "b": "v"}

    def test_repeated_query_parameters_are_all_available(self, make_client):
        """``getlist`` keeps every value; scalar access takes the last, as
        Starlette and Django do, so a later value overrides an earlier one."""
        app = SlowAPI()

        @app.get("/x")
        def handler(req, res: Response):
            return res.json({"all": req.query.getlist("t"), "one": req.query.get("t")})

        body = make_client(app).get("/x?t=a&t=b").json()
        assert body == {"all": ["a", "b"], "one": "b"}

    def test_percent_encoding_is_decoded_in_path_parameters(self, make_client):
        app = SlowAPI()

        @app.get("/items/{name}")
        def handler(name: str, res: Response):
            return res.json({"name": name})

        assert make_client(app).get("/items/a%20b").json() == {"name": "a b"}

    def test_url_for_builds_a_path_from_a_route_name(self):
        app = SlowAPI()

        @app.get("/users/{id:int}", name="user-detail")
        def handler(id: int, res: Response):
            return res.json({})

        assert app.url_for("user-detail", id=7) == "/users/7"

    def test_state_carries_values_between_middleware_and_handler(self, make_client):
        app = SlowAPI()

        def stamp(req, res):
            req.state.tenant = "acme"

        app.use(stamp)

        @app.get("/x")
        def handler(req, res: Response):
            return res.json({"tenant": req.state.tenant})

        assert make_client(app).get("/x").json() == {"tenant": "acme"}


class TestContentTypes:
    def test_json_is_the_default_for_a_returned_object(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return {"a": 1}

        response = make_client(app).get("/x")
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"a": 1}

    def test_a_returned_string_is_not_silently_json(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.text("hello")

        response = make_client(app).get("/x")
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == "hello"

    def test_html_is_served_as_html(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.html("<p>hi</p>")

        assert make_client(app).get("/x").headers["content-type"].startswith("text/html")

    def test_a_json_body_survives_a_round_trip_with_unicode(self, make_client):
        app = SlowAPI()

        @app.post("/echo")
        def echo(req, res: Response):
            from slowapi.concurrency import call_maybe_sync

            return res.json(call_maybe_sync(req.json))

        payload = {"name": "Ada Lovelace", "note": "café — 日本語"}
        assert make_client(app).post("/echo", json=payload).json() == payload


class TestValidationCoercion:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("n=42", 42),
            ("n=-7", -7),
            ("n=0", 0),
        ],
    )
    def test_integers_are_coerced(self, make_client, query, expected):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, n: int = 0):
            return res.json({"n": n})

        assert make_client(app).get(f"/x?{query}").json() == {"n": expected}

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on"])
    def test_truthy_spellings_of_a_bool(self, make_client, raw):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, flag: bool = False):
            return res.json({"flag": flag})

        assert make_client(app).get(f"/x?flag={raw}").json() == {"flag": True}

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off"])
    def test_falsy_spellings_of_a_bool(self, make_client, raw):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, flag: bool = True):
            return res.json({"flag": flag})

        assert make_client(app).get(f"/x?flag={raw}").json() == {"flag": False}

    def test_a_value_of_the_wrong_type_names_the_parameter(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, n: int = 0):
            return res.json({})

        response = make_client(app).get("/x?n=many")

        assert response.status_code == 422
        detail = response.json()["error"]["details"][0]
        assert detail["loc"] == ["query", "n"]

    def test_constraints_are_enforced(self, make_client):
        from slowapi import Query

        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, n: int = Query(1, ge=1, le=10)):
            return res.json({"n": n})

        client = make_client(app)
        assert client.get("/x?n=5").json() == {"n": 5}
        assert client.get("/x?n=0").status_code == 422
        assert client.get("/x?n=11").status_code == 422

    def test_an_optional_parameter_may_be_absent(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, q: str | None = None):
            return res.json({"q": q})

        assert make_client(app).get("/x").json() == {"q": None}

    def test_a_uuid_path_converter_rejects_a_non_uuid(self, make_client):
        app = SlowAPI()

        @app.get("/items/{id:uuid}")
        def handler(id, res: Response):
            return res.json({"id": str(id)})

        client = make_client(app)
        assert client.get("/items/not-a-uuid").status_code == 404
        good = "123e4567-e89b-12d3-a456-426614174000"
        assert client.get(f"/items/{good}").json() == {"id": good}


class TestOpenAPI:
    def test_the_document_describes_the_registered_routes(self):
        app = SlowAPI(title="Billing", version="2.0.0")

        @app.get("/items/{id:int}", summary="Fetch one item")
        def handler(id: int, res: Response, q: str = ""):
            return res.json({})

        document = app.openapi()

        assert document["info"]["title"] == "Billing"
        assert document["info"]["version"] == "2.0.0"
        operation = document["paths"]["/items/{id}"]["get"]
        assert operation["summary"] == "Fetch one item"
        names = {p["name"] for p in operation["parameters"]}
        assert names == {"id", "q"}

    def test_a_dto_becomes_a_request_schema(self):
        app = SlowAPI()

        @app.post("/items")
        def create(item: Item, res: Response):
            return res.json({})

        document = app.openapi()
        assert "requestBody" in document["paths"]["/items"]["post"]

    def test_the_document_is_valid_json(self):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        assert json.loads(json.dumps(app.openapi()))["openapi"].startswith("3.1")


class TestPathDecoding:
    """Percent-encoded path segments used to reach handlers still encoded."""

    def test_a_space_arrives_decoded(self, make_client):
        app = SlowAPI()

        @app.get("/items/{name}")
        def handler(name: str, res: Response):
            return res.json({"name": name})

        assert make_client(app).get("/items/a%20b").json() == {"name": "a b"}

    def test_reserved_characters_arrive_decoded(self, make_client):
        app = SlowAPI()

        @app.get("/users/{email}")
        def handler(email: str, res: Response):
            return res.json({"email": email})

        assert make_client(app).get("/users/ada%40example.com").json() == {
            "email": "ada@example.com"
        }

    def test_an_encoded_separator_cannot_forge_a_route(self, make_client):
        """The reason segments are split before they are decoded."""
        app = SlowAPI()

        @app.get("/admin/delete")
        def handler(res: Response):
            return res.json({"reached": True})

        # %2F must stay inside one segment rather than becoming a path boundary.
        assert make_client(app).get("/admin%2Fdelete").status_code == 404

    def test_a_path_converter_still_spans_segments(self, make_client):
        app = SlowAPI()

        @app.get("/files/{rest:path}")
        def handler(rest: str, res: Response):
            return res.json({"rest": rest})

        assert make_client(app).get("/files/a/b/c.txt").json() == {"rest": "a/b/c.txt"}


class TestConditionalRequests:
    def test_the_chain_order_does_not_change_the_tag(self, make_client):
        """``etag()`` before the body used to hash an empty body -- one tag
        shared by every such response, which is a cache-poisoning bug wearing
        the costume of a working ETag."""
        app = SlowAPI()

        @app.get("/before")
        def before(res: Response):
            return res.etag().json({"a": 1})

        @app.get("/after")
        def after(res: Response):
            return res.json({"a": 1}).etag()

        client = make_client(app)
        assert client.get("/before").headers["etag"] == client.get("/after").headers["etag"]

    def test_two_different_bodies_get_two_different_tags(self, make_client):
        app = SlowAPI()

        @app.get("/a")
        def a(res: Response):
            return res.etag().json({"v": 1})

        @app.get("/b")
        def b(res: Response):
            return res.etag().json({"v": 2})

        client = make_client(app)
        assert client.get("/a").headers["etag"] != client.get("/b").headers["etag"]

    def test_a_matching_tag_returns_304_with_no_body(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        client = make_client(app)
        etag = client.get("/x").headers["etag"]
        response = client.get("/x", headers={"if-none-match": etag})

        assert response.status_code == 304
        assert response.content == b""
        assert "content-length" not in response.headers

    def test_a_weak_tag_matches_its_strong_form(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        client = make_client(app)
        etag = client.get("/x").headers["etag"]
        assert client.get("/x", headers={"if-none-match": f"W/{etag}"}).status_code == 304

    def test_a_star_matches_any_tag(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        assert make_client(app).get("/x", headers={"if-none-match": "*"}).status_code == 304

    def test_one_tag_out_of_a_list_is_enough(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        client = make_client(app)
        etag = client.get("/x").headers["etag"]
        header = f'"other", {etag}, "another"'
        assert client.get("/x", headers={"if-none-match": header}).status_code == 304

    def test_a_stale_tag_gets_the_full_body(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.etag().json({"a": 1})

        response = make_client(app).get("/x", headers={"if-none-match": '"stale"'})
        assert response.status_code == 200
        assert response.json() == {"a": 1}

    def test_writes_are_never_downgraded_to_304(self, make_client):
        """Answering a POST with 304 would report the write as a no-op."""
        app = SlowAPI()

        @app.post("/x")
        def handler(res: Response):
            return res.etag().json({"created": True})

        client = make_client(app)
        etag = client.post("/x", json={}).headers["etag"]
        assert client.post("/x", json={}, headers={"if-none-match": etag}).status_code != 304

    def test_if_modified_since_is_honoured_when_there_is_no_etag(self, make_client):
        app = SlowAPI()
        stamp = "Wed, 21 Oct 2015 07:28:00 GMT"

        @app.get("/x")
        def handler(res: Response):
            return res.set("last-modified", stamp).json({"a": 1})

        client = make_client(app)
        assert client.get("/x", headers={"if-modified-since": stamp}).status_code == 304
        assert client.get("/x").status_code == 200

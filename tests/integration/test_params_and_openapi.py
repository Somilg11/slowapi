"""Parameter sourcing, dependency injection into handlers, and the schema."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from slowapi import Body, Cookie, Depends, Header, Query, SlowAPI, UploadFile
from slowapi.params import File


@dataclass
class Item:
    name: str
    qty: int = 1


@pytest.fixture
def app() -> SlowAPI:
    app = SlowAPI(title="Params", version="9.9.9", description="fixture app")

    @app.get("/q")
    def q(term: str = Query(..., min_length=2), page: int = Query(1, ge=1)) -> dict:
        return {"term": term, "page": page}

    @app.get("/tags")
    def tags(tag: list[str] = Query(default_factory=list)) -> dict:
        return {"tags": tag}

    @app.get("/h")
    def h(x_api_key: str = Header(...)) -> dict:
        return {"key": x_api_key}

    @app.get("/c")
    def c(sid: str = Cookie("anonymous")) -> dict:
        return {"sid": sid}

    @app.post("/body")
    def body(item: Item) -> dict:
        return {"name": item.name, "qty": item.qty}

    @app.post("/embedded")
    def embedded(name: str = Body(..., embed=True)) -> dict:
        return {"name": name}

    @app.post("/upload")
    async def upload(document: UploadFile = File(...)) -> dict:
        content = await document.read()
        return {"filename": document.filename, "size": len(content)}

    def pagination(limit: int = Query(10, le=100), offset: int = Query(0, ge=0)) -> dict:
        return {"limit": limit, "offset": offset}

    @app.get("/page")
    def page(paging: dict = Depends(pagination)) -> dict:
        return paging

    return app


class TestParameterSources:
    def test_query_parameters_are_coerced_and_defaulted(self, app, make_client):
        client = make_client(app)
        assert client.get("/q", params={"term": "hi"}).json() == {"term": "hi", "page": 1}
        assert client.get("/q", params={"term": "hi", "page": "4"}).json()["page"] == 4

    def test_constraint_failures_report_the_location(self, app, make_client):
        detail = make_client(app).get("/q", params={"term": "x"}).json()["error"]["details"][0]
        assert detail["loc"] == ["query", "term"]

    def test_repeated_query_keys_collect_into_a_list(self, app, make_client):
        assert make_client(app).get("/tags?tag=a&tag=b").json() == {"tags": ["a", "b"]}

    def test_header_names_convert_underscores_to_dashes(self, app, make_client):
        assert make_client(app).get("/h", headers={"x-api-key": "k"}).json() == {"key": "k"}

    def test_a_missing_required_header_is_a_422(self, app, make_client):
        assert make_client(app).get("/h").status_code == 422

    def test_cookies_fall_back_to_their_default(self, app, make_client):
        client = make_client(app)
        assert client.get("/c").json() == {"sid": "anonymous"}
        assert client.get("/c", cookies={"sid": "abc"}).json() == {"sid": "abc"}

    def test_a_dto_annotation_reads_the_whole_body(self, app, make_client):
        assert make_client(app).post("/body", json={"name": "x", "qty": 2}).json() == {
            "name": "x",
            "qty": 2,
        }

    def test_an_embedded_scalar_reads_one_key(self, app, make_client):
        assert make_client(app).post("/embedded", json={"name": "x"}).json() == {"name": "x"}

    def test_multipart_uploads_are_parsed(self, app, make_client):
        body = (
            b'--B\r\nContent-Disposition: form-data; name="document"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\nhello\r\n--B--\r\n"
        )
        response = make_client(app).post(
            "/upload", data=body, headers={"content-type": "multipart/form-data; boundary=B"}
        )
        assert response.json() == {"filename": "a.txt", "size": 5}


class TestDependencies:
    def test_a_dependency_gets_its_own_parameters_resolved(self, app, make_client):
        assert make_client(app).get("/page", params={"limit": "5"}).json() == {
            "limit": 5,
            "offset": 0,
        }

    def test_constraints_inside_a_dependency_still_apply(self, app, make_client):
        assert make_client(app).get("/page", params={"limit": "500"}).status_code == 422


class TestOpenAPI:
    def test_the_document_describes_the_running_code(self, app):
        document = app.openapi()
        assert document["openapi"] == "3.1.0"
        assert document["info"]["version"] == "9.9.9"

        operation = document["paths"]["/q"]["get"]
        by_name = {p["name"]: p for p in operation["parameters"]}
        assert by_name["term"]["required"] is True
        assert by_name["term"]["schema"]["minLength"] == 2
        assert by_name["page"]["required"] is False
        assert by_name["page"]["schema"]["default"] == 1

    def test_models_are_referenced_not_inlined(self, app):
        body = app.openapi()["paths"]["/body"]["post"]["requestBody"]
        assert body["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Item"
        }
        assert app.openapi()["components"]["schemas"]["Item"]["required"] == ["name"]

    def test_dependency_parameters_appear_in_the_schema(self, app):
        names = {p["name"] for p in app.openapi()["paths"]["/page"]["get"]["parameters"]}
        assert names == {"limit", "offset"}

    def test_docs_routes_are_served_but_not_documented(self, app, make_client):
        client = make_client(app)
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert "/docs" not in app.openapi()["paths"]

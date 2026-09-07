"""Path compilation, matching, and reverse resolution."""

from __future__ import annotations

import pytest

from slowfw.exceptions import ConfigurationError, MethodNotAllowed, NotFound
from slowfw.routing import Route, Router, compile_path


def handler() -> None:  # pragma: no cover - never invoked
    pass


def make(path: str, method: str = "GET", name: str | None = None) -> Route:
    return Route(path=path, method=method, handler=handler, name=name or path)


class TestCompilePath:
    def test_both_syntaxes_normalise_to_one_canonical_form(self):
        _, _, brace = compile_path("/users/{id}")
        _, _, colon = compile_path("/users/:id")
        assert brace == colon == "/users/{id}"

    def test_typed_converters_are_recorded(self):
        _, params, _ = compile_path("/posts/{year:int}/{slug:slug}")
        assert [p.name for p in params] == ["year", "slug"]
        assert params[0].convertor.convert("2024") == 2024

    def test_path_must_start_with_slash(self):
        with pytest.raises(ConfigurationError, match="must start with"):
            compile_path("users/{id}")

    def test_unknown_converter_names_the_alternatives(self):
        with pytest.raises(ConfigurationError, match="Unknown path converter"):
            compile_path("/x/{id:banana}")

    def test_duplicate_parameter_names_are_rejected(self):
        with pytest.raises(ConfigurationError, match="Duplicate path parameter"):
            compile_path("/x/{id}/y/{id}")


class TestMatching:
    def test_literal_beats_parameter(self):
        router = Router()
        router.add(make("/users/me", name="me"))
        router.add(make("/users/{id:int}", name="by_id"))
        assert router.match("GET", "/users/me")[0].name == "me"
        assert router.match("GET", "/users/7")[0].name == "by_id"

    def test_converter_mismatch_falls_through_to_another_route(self):
        router = Router()
        router.add(make("/x/{n:int}", name="numeric"))
        router.add(make("/x/{s}", name="textual"))
        assert router.match("GET", "/x/12")[0].name == "numeric"
        assert router.match("GET", "/x/abc")[0].name == "textual"

    def test_values_are_converted_not_just_captured(self):
        router = Router()
        router.add(make("/x/{n:int}"))
        _, params = router.match("GET", "/x/42")
        assert params == {"n": 42}
        assert isinstance(params["n"], int)

    def test_greedy_path_converter_spans_slashes(self):
        router = Router()
        router.add(make("/files/{path:path}"))
        _, params = router.match("GET", "/files/a/b/c.txt")
        assert params == {"path": "a/b/c.txt"}

    def test_path_converter_must_come_last(self):
        router = Router()
        with pytest.raises(ConfigurationError, match="must be the last segment"):
            router.add(make("/files/{path:path}/edit"))

    def test_partial_segment_parameters(self):
        router = Router()
        router.add(make("/report.{fmt}"))
        _, params = router.match("GET", "/report.csv")
        assert params == {"fmt": "csv"}

    def test_unknown_path_raises_not_found(self):
        with pytest.raises(NotFound):
            Router().match("GET", "/nowhere")

    def test_wrong_method_reports_the_allowed_set(self):
        router = Router()
        router.add(make("/x", "GET"))
        router.add(make("/x", "POST"))
        with pytest.raises(MethodNotAllowed) as info:
            router.match("DELETE", "/x")
        assert info.value.headers["Allow"] == "GET, OPTIONS, POST"

    def test_head_falls_back_to_get(self):
        router = Router()
        router.add(make("/x", "GET", name="get_x"))
        assert router.match("HEAD", "/x")[0].name == "get_x"

    def test_duplicate_registration_is_rejected(self):
        router = Router()
        router.add(make("/x"))
        with pytest.raises(ConfigurationError, match="Duplicate route"):
            router.add(make("/x"))


class TestReverse:
    def test_url_path_for_substitutes_parameters(self):
        router = Router()
        router.add(make("/users/{id:int}", name="user"))
        assert router.url_path_for("user", id=7) == "/users/7"

    def test_missing_parameter_is_an_error(self):
        router = Router()
        router.add(make("/users/{id}", name="user"))
        with pytest.raises(ConfigurationError, match="missing path parameter"):
            router.url_path_for("user")

    def test_unknown_name_lists_what_exists(self):
        router = Router()
        router.add(make("/a", name="alpha"))
        with pytest.raises(ConfigurationError, match="alpha"):
            router.url_path_for("nope")

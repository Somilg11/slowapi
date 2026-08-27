"""Coercion, constraint checking, and schema generation."""

from __future__ import annotations

import typing as t
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pytest

from slowapi.exceptions import ValidationError
from slowapi.params import Query
from slowapi.validation import FieldError, coerce, json_schema_for, validate_param


class Colour(str, Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Address:
    city: str
    postcode: str = "00000"


@dataclass
class Person:
    name: str
    age: int
    address: Address | None = None
    tags: list[str] = field(default_factory=list)


class TestScalars:
    @pytest.mark.parametrize(
        ("raw", "annotation", "expected"),
        [
            ("42", int, 42),
            ("3.5", float, 3.5),
            ("true", bool, True),
            ("OFF", bool, False),
            ("red", Colour, Colour.RED),
            (
                "2024-01-02T03:04:05Z",
                datetime,
                datetime(
                    2024,
                    1,
                    2,
                    3,
                    4,
                    5,
                    tzinfo=t.cast(
                        t.Any, datetime.fromisoformat("2024-01-02T03:04:05+00:00")
                    ).tzinfo,
                ),
            ),
        ],
    )
    def test_wire_text_becomes_the_declared_type(self, raw, annotation, expected):
        assert coerce(raw, annotation) == expected

    def test_uuid_round_trips(self):
        value = uuid.uuid4()
        assert coerce(str(value), uuid.UUID) == value

    def test_nonsense_is_rejected_with_a_useful_message(self):
        with pytest.raises(FieldError) as info:
            coerce("three", int, ("query", "n"))
        assert info.value.loc == ("query", "n")
        assert "Not a valid int" in info.value.message

    def test_ambiguous_boolean_text_is_an_error_not_a_guess(self):
        with pytest.raises(FieldError):
            coerce("maybe", bool)


class TestContainers:
    def test_list_items_are_coerced_individually(self):
        assert coerce(["1", "2"], list[int]) == [1, 2]

    def test_a_scalar_is_promoted_to_a_single_item_list(self):
        assert coerce("a", list[str]) == ["a"]

    def test_optional_accepts_none(self):
        assert coerce(None, int | None) is None

    def test_literal_restricts_the_value_set(self):
        assert coerce("asc", t.Literal["asc", "desc"]) == "asc"
        with pytest.raises(FieldError):
            coerce("sideways", t.Literal["asc", "desc"])

    def test_dict_keys_and_values_are_coerced(self):
        assert coerce({"1": "2"}, dict[int, int]) == {1: 2}


class TestDataclasses:
    def test_nested_models_are_built_recursively(self):
        person = coerce({"name": "Ada", "age": "36", "address": {"city": "London"}}, Person)
        assert person.age == 36
        assert person.address.city == "London"
        assert person.address.postcode == "00000"

    def test_every_field_error_is_reported_at_once(self):
        with pytest.raises(ValidationError) as info:
            coerce({"age": "old"}, Person)
        locations = {tuple(e["loc"]) for e in info.value.errors}
        assert locations == {("name",), ("age",)}

    def test_defaults_fill_in_for_absent_fields(self):
        assert coerce({"name": "x", "age": 1}, Person).tags == []


class TestConstraints:
    def test_numeric_bounds(self):
        marker = Query(0, ge=1, le=10)
        assert validate_param(5, marker, ()) == 5
        with pytest.raises(FieldError, match="Must be >= 1"):
            validate_param(0, marker, ())

    def test_string_length_and_pattern(self):
        marker = Query("", min_length=2, pattern=r"^[a-z]+$")
        assert validate_param("ab", marker, ()) == "ab"
        with pytest.raises(FieldError, match="at least 2"):
            validate_param("a", marker, ())
        with pytest.raises(FieldError, match="Must match"):
            validate_param("AB", marker, ())


class TestSchema:
    def test_primitives_map_to_json_types(self):
        assert json_schema_for(int) == {"type": "integer"}
        assert json_schema_for(list[str]) == {"type": "array", "items": {"type": "string"}}

    def test_models_are_hoisted_into_components(self):
        components: dict = {}
        schema = json_schema_for(Person, components)
        assert schema == {"$ref": "#/components/schemas/Person"}
        assert components["Person"]["required"] == ["name", "age"]
        assert "Address" in components

    def test_enums_become_enum_schemas(self):
        assert json_schema_for(Colour)["enum"] == ["red", "blue"]

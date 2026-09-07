"""Type coercion: the layer that turns wire strings into Python values.

Every request goes through here, so the surprising inputs are the point --
an empty string for an int, a date in a different format, a nested model, a
value that is already the right type.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from slowfw.validation import FieldError, coerce, dump, is_dto, json_schema_for


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclasses.dataclass
class Address:
    city: str
    postcode: str = ""


@dataclasses.dataclass
class Person:
    name: str
    age: int
    address: Address | None = None
    tags: list[str] = dataclasses.field(default_factory=list)


class TestScalars:
    @pytest.mark.parametrize(
        ("raw", "annotation", "expected"),
        [
            ("42", int, 42),
            (42, int, 42),
            ("3.5", float, 3.5),
            ("x", str, "x"),
            (5, str, "5"),
            ("10.25", Decimal, Decimal("10.25")),
        ],
    )
    def test_common_conversions(self, raw, annotation, expected):
        assert coerce(raw, annotation, ("query", "v")) == expected

    def test_an_untyped_value_passes_through(self):
        marker = object()
        assert coerce(marker, None, ()) is marker

    @pytest.mark.parametrize("raw", ["", "abc", "1.2.3"])
    def test_an_uncoercible_int_names_the_field(self, raw):
        with pytest.raises(FieldError) as info:
            coerce(raw, int, ("query", "n"))
        assert info.value.loc == ("query", "n")

    def test_a_uuid_is_parsed(self):
        value = "123e4567-e89b-12d3-a456-426614174000"
        assert coerce(value, uuid.UUID, ()) == uuid.UUID(value)

    def test_an_enum_accepts_its_value(self):
        assert coerce("red", Colour, ()) is Colour.RED

    def test_an_enum_rejects_anything_else(self):
        with pytest.raises(FieldError):
            coerce("green", Colour, ("query", "colour"))


class TestDatesAndTimes:
    @pytest.mark.parametrize(
        "raw",
        [
            "2024-01-15T10:30:00",
            "2024-01-15T10:30:00Z",
            "2024-01-15T10:30:00+00:00",
        ],
    )
    def test_iso_datetimes_are_accepted(self, raw):
        parsed = coerce(raw, datetime, ())
        assert parsed.year == 2024 and parsed.hour == 10

    def test_a_unix_timestamp_is_accepted(self):
        """Clients that send epoch seconds are common enough to support."""
        parsed = coerce(1700000000, datetime, ())
        assert isinstance(parsed, datetime)

    def test_dates_and_times_parse_on_their_own(self):
        assert coerce("2024-01-15", date, ()) == date(2024, 1, 15)
        assert coerce("10:30:00", time, ()) == time(10, 30)

    def test_a_malformed_datetime_is_a_field_error(self):
        with pytest.raises(FieldError):
            coerce("yesterday", datetime, ("query", "when"))


class TestBooleans:
    @pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on", True, 1])
    def test_truthy(self, raw):
        assert coerce(raw, bool, ()) is True

    @pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off", False, 0])
    def test_falsy(self, raw):
        assert coerce(raw, bool, ()) is False

    def test_anything_else_is_rejected(self):
        """``bool("maybe")`` is ``True`` in Python, which is not what the
        client meant and not something to guess at."""
        with pytest.raises(FieldError):
            coerce("maybe", bool, ("query", "flag"))


class TestContainers:
    def test_a_list_coerces_each_item(self):
        assert coerce(["1", "2"], list[int], ()) == [1, 2]

    def test_a_set_deduplicates(self):
        assert coerce(["1", "1", "2"], set[int], ()) == {1, 2}

    def test_a_dict_coerces_its_values(self):
        assert coerce({"a": "1"}, dict[str, int], ()) == {"a": 1}

    def test_a_bad_item_reports_its_index(self):
        with pytest.raises(FieldError) as info:
            coerce(["1", "x"], list[int], ("query", "ns"))
        assert "1" in str(info.value.loc)

    def test_optional_accepts_none(self):
        assert coerce(None, int | None, ()) is None
        assert coerce("5", int | None, ()) == 5


class TestDataclasses:
    def test_a_flat_model_is_built(self):
        person = coerce({"name": "Ada", "age": "36"}, Person, ())
        assert person.name == "Ada" and person.age == 36

    def test_a_nested_model_is_built(self):
        person = coerce({"name": "Ada", "age": 36, "address": {"city": "London"}}, Person, ())
        assert isinstance(person.address, Address)
        assert person.address.city == "London"

    def test_every_missing_field_is_reported_at_once(self):
        from slowfw.exceptions import ValidationError

        with pytest.raises(ValidationError) as info:
            coerce({}, Person, ("body",))

        fields = {tuple(detail["loc"])[-1] for detail in info.value.errors}
        assert fields == {"name", "age"}

    def test_unknown_keys_are_ignored_rather_than_fatal(self):
        """A client sending an extra field should not break a working call."""
        person = coerce({"name": "Ada", "age": 1, "surprise": True}, Person, ())
        assert person.name == "Ada"

    def test_an_instance_passes_through(self):
        original = Person(name="Ada", age=36)
        assert coerce(original, Person, ()) is original

    def test_is_dto_recognises_a_dataclass_and_nothing_else(self):
        assert is_dto(Person)
        assert not is_dto(int)
        assert not is_dto(list[str])


class TestDump:
    def test_a_dataclass_becomes_a_dict(self):
        assert dump(Person(name="Ada", age=36))["name"] == "Ada"

    def test_nested_values_are_dumped_too(self):
        payload = dump(Person(name="Ada", age=36, address=Address(city="London")))
        assert payload["address"]["city"] == "London"

    def test_types_json_cannot_hold_are_stringified(self):
        payload = dump({"id": uuid.uuid4(), "when": datetime(2024, 1, 1), "n": Decimal("1.5")})
        import json

        json.dumps(payload)  # must not raise

    def test_an_enum_dumps_as_its_value(self):
        assert dump(Colour.RED) == "red"


class TestJSONSchema:
    def test_a_dataclass_becomes_an_object_schema(self):
        schema = json_schema_for(Person)
        assert schema["type"] == "object"
        assert "name" in schema["properties"]

    def test_required_fields_are_marked(self):
        schema = json_schema_for(Person)
        assert set(schema.get("required", [])) == {"name", "age"}

    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [(int, "integer"), (float, "number"), (str, "string"), (bool, "boolean")],
    )
    def test_scalars_map_to_json_types(self, annotation, expected):
        assert json_schema_for(annotation)["type"] == expected

    def test_a_list_declares_its_item_type(self):
        schema = json_schema_for(list[int])
        assert schema["type"] == "array" and schema["items"]["type"] == "integer"

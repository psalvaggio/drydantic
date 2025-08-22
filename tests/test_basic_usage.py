"""Unit tests for basic usage of the library"""

import typing as ty

import pydantic
import pytest

import drydantic


# Define your models - explicitly add the defaults field
class Inner(pydantic.BaseModel):
    """Test list item class"""

    a: int
    b: int
    c: str = "default"


class Outer(pydantic.BaseModel, drydantic.DefaultsMergeMixin):
    """Test container class"""

    inners: ty.Annotated[
        list[Inner],
        pydantic.Field(description="The inner list"),
        drydantic.supports_defaults("inners_defaults"),
    ]
    other_list: list[int] = []
    name: str = "test"


@pytest.mark.parametrize(
    ("input_data", "result"),
    [
        pytest.param(
            {
                "inners_defaults": {"b": 1, "c": "from_defaults"},
                "inners": [
                    {"a": 1},  # Will get b=1, c="from_defaults"
                    {"a": 2, "b": 5},  # Will get b=5 (overridden), c="from_defaults"
                    {"a": 3, "b": 2, "c": "custom"},  # All values explicit
                ],
            },
            Outer(
                inners=[
                    Inner(a=1, b=1, c="from_defaults"),
                    Inner(a=2, b=5, c="from_defaults"),
                    Inner(a=3, b=2, c="custom"),
                ],
            ),
            id="basic",
        ),
        pytest.param(
            {
                "inners": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
            },
            Outer(inners=[Inner(a=1, b=2), Inner(a=3, b=4)]),
            id="no-defaults",
        ),
    ],
)
def test_simple_validation(input_data: dict[str, ty.Any], result: Outer) -> None:
    """Test a simple pydantic validation"""
    outer = Outer(**input_data)
    assert outer == result


def test_json_schema() -> None:
    """Test the JSON schema"""
    schema = Outer.model_json_schema()
    assert "Inner" in schema["$defs"]
    assert schema["$defs"]["Inner"]["required"] == ["a", "b"]
    assert "PartialInner" in schema["$defs"]
    assert "required" not in schema["$defs"]["PartialInner"]
    assert "inners" in schema["properties"]
    assert "inners" in schema["required"]
    assert "inners_defaults" in schema["properties"]
    assert "inners_defaults" not in schema["required"]
    assert schema["properties"]["inners"]["items"]["anyOf"] == [
        {"$ref": "#/$defs/PartialInner"},
        {"$ref": "#/$defs/Inner"},
    ]
    assert schema["properties"]["inners_defaults"]["anyOf"] == [
        {"$ref": "#/$defs/PartialInner"},
        {"type": "null"},
    ]

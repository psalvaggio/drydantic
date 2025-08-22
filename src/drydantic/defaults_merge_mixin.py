"""
Pydantic library for supporting defaults merging with type annotations.

This library allows you to specify default values that get merged with list items
before validation, using a clean type annotation interface.
"""

import copy
import dataclasses
import typing as ty
from typing import get_args, get_origin

import pydantic


@dataclasses.dataclass
class SupportsDefaults:
    """Marker class to indicate a field supports default merging"""

    defaults_field: str


def supports_defaults(defaults_field: str) -> SupportsDefaults:
    """Type annotation helper for fields that support default merging.

    Parameters
    ----------
    defaults_field : str
        Name for the defaults field

    Returns
    -------
    SupportsDefaults
        Annotation for the field
    """
    return SupportsDefaults(defaults_field=defaults_field)


def _extract_defaults_fields(cls: ty.Any) -> list[tuple]:
    """Extract fields that have SupportsDefaults annotation."""
    defaults_fields = []

    # Get type hints from the class
    try:
        hints = ty.get_type_hints(cls, include_extras=True)
    except (NameError, AttributeError):
        hints = {}

    for field_name, annotation in hints.items():
        # Handle typing.Annotated
        if get_origin(annotation) is ty.Annotated:
            args = get_args(annotation)
            if len(args) > 1:
                for metadata in args[1:]:
                    if isinstance(metadata, SupportsDefaults):
                        defaults_fields.append((field_name, metadata))
                        break

    return defaults_fields


def _get_inner_type_from_list_annotation(cls: type, field_name: str) -> type | None:
    """Extract the inner type from a list[T] annotation."""
    try:
        hints = ty.get_type_hints(cls, include_extras=True)
        annotation = hints.get(field_name)

        if annotation:
            # Handle Annotated[list[T], ...]
            if get_origin(annotation) is ty.Annotated:
                args = get_args(annotation)
                if args:
                    list_type = args[0]
                else:
                    return None
            else:
                list_type = annotation

            # Handle list[T]
            if get_origin(list_type) is list:
                list_args = get_args(list_type)
                if list_args:
                    return list_args[0]
    except (TypeError, ValueError):
        pass

    return None


class DefaultsMergeMixin:
    """Mixin that adds default merging capabilities to Pydantic models."""

    @pydantic.model_validator(mode="before")
    @classmethod
    def _merge_defaults(cls, data: ty.Any) -> ty.Any:
        if not isinstance(data, dict):
            return data

        # Get the defaults fields info
        defaults_fields = _extract_defaults_fields(cls)
        if not defaults_fields:
            return data

        # Process each field that supports defaults
        result_data = dict(data)

        for field_name, supports_defaults_info in defaults_fields:
            defaults_key = supports_defaults_info.defaults_field

            if field_name in result_data and defaults_key in result_data:
                field_data = result_data[field_name]
                defaults_data = result_data[defaults_key]

                if isinstance(field_data, list) and isinstance(defaults_data, dict):
                    # Merge defaults with each item in the list
                    merged_items = []
                    for item in field_data:
                        if isinstance(item, dict):
                            # Create a new dict with defaults as base, then
                            # update with item
                            merged_item = copy.deepcopy(defaults_data)
                            merged_item.update(item)
                            merged_items.append(merged_item)
                        else:
                            merged_items.append(item)

                    result_data[field_name] = merged_items

                # Remove the defaults field from the final data
                result_data.pop(defaults_key, None)

        return result_data

    def __init_subclass__(cls, **kwargs) -> None:
        """Subclass hook"""
        super().__init_subclass__(**kwargs)

        # Store the original model_json_schema method
        original_schema_method = cls.model_json_schema

        @classmethod
        def custom_model_json_schema(
            cls_inner: type,
            by_alias: bool = True,  # noqa: FBT001, FBT002 (inherited)
            ref_template: str = "#/$defs/{model}",
        ) -> dict[str, ty.Any]:
            """Override to customize schema generation for defaults fields."""
            # First get the base schema
            schema = original_schema_method(
                by_alias=by_alias,
                ref_template=ref_template,
            )

            # Get defaults fields
            defaults_fields = _extract_defaults_fields(cls_inner)

            # Add defaults fields to schema manually if they don't exist
            for field_name, supports_defaults_info in defaults_fields:
                defaults_key = supports_defaults_info.defaults_field
                inner_type = _get_inner_type_from_list_annotation(cls_inner, field_name)

                # Add defaults field to schema if not present
                if "properties" not in schema:
                    schema["properties"] = {}

                if defaults_key not in schema["properties"]:
                    schema["properties"][defaults_key] = {
                        "anyOf": [
                            {"type": "object", "additionalProperties": True},
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": defaults_key.replace("_", " ").title(),
                        "description": (
                            f"Default values to merge with each item in {field_name}"
                        ),
                    }

                # Remove from required if present
                if "required" in schema and defaults_key in schema["required"]:
                    schema["required"].remove(defaults_key)

                if inner_type and hasattr(inner_type, "model_json_schema"):
                    # Get the full schema for the inner type
                    inner_schema = inner_type.model_json_schema(
                        by_alias=by_alias,
                        ref_template=ref_template,
                    )

                    # Create a partial version where no fields are required
                    partial_schema = copy.deepcopy(inner_schema)
                    partial_schema.pop("required", None)  # Remove all required fields
                    partial_schema["title"] = f"Partial{inner_type.__name__}"
                    partial_schema["description"] = (
                        f"Partial {inner_type.__name__} - fields will be "
                        "merged with defaults. After merging, the result must "
                        f"satisfy the full {inner_type.__name__} schema."
                    )

                    # Add the partial schema to definitions
                    if "$defs" not in schema:
                        schema["$defs"] = {}
                    schema["$defs"][f"Partial{inner_type.__name__}"] = partial_schema

                    # Update the defaults field to reference the partial schema
                    schema["properties"][defaults_key] = {
                        "anyOf": [
                            {"$ref": f"#/$defs/Partial{inner_type.__name__}"},
                            {"type": "null"},
                        ],
                        "default": None,
                        "description": (
                            "Default values to merge with each item in "
                            f"{field_name}. After merging with list items, "
                            "results must satisfy the full "
                            f"{inner_type.__name__} schema."
                        ),
                        "title": f"{defaults_key.replace('_', ' ').title()}",
                    }

                    # Update the main list field to use partial items but
                    # document full validation
                    if field_name in schema["properties"]:
                        original_desc = schema["properties"][field_name].get(
                            "description",
                            "",
                        )
                        merge_desc = (
                            f"Each item is merged with {defaults_key} before "
                            "validation. Individual items can be partial, but "
                            "after merging must satisfy the full "
                            f"{inner_type.__name__} schema."
                        )

                        schema["properties"][field_name]["description"] = (
                            f"{original_desc} {merge_desc}".strip()
                        )

                        # Also update the items schema to reference the partial
                        if "items" in schema["properties"][field_name]:
                            schema["properties"][field_name]["items"] = {
                                "anyOf": [
                                    {"$ref": f"#/$defs/Partial{inner_type.__name__}"},
                                    {"$ref": f"#/$defs/{inner_type.__name__}"},
                                ],
                                "description": (
                                    "Partial or complete "
                                    f"{inner_type.__name__} item. Will be "
                                    "merged with defaults before validation."
                                ),
                            }

            return schema

        # Replace the method on the class
        cls.model_json_schema = custom_model_json_schema


# Example usage and test cases
if __name__ == "__main__":
    import json

    # Define your models - explicitly add the defaults field
    class Inner(pydantic.BaseModel):
        """Test"""

        a: int
        b: int
        c: str = "default"

    class Outer(pydantic.BaseModel, DefaultsMergeMixin):
        """Test"""

        inners: ty.Annotated[list[Inner], supports_defaults("inners_defaults")]
        name: str = "test"

    # Test the functionality
    test_data = {
        "inners_defaults": {"b": 1, "c": "from_defaults"},
        "inners": [
            {"a": 1},  # Will get b=1, c="from_defaults"
            {"a": 2, "b": 5},  # Will get b=5 (overridden), c="from_defaults"
            {"a": 3, "b": 2, "c": "custom"},  # All values explicit
        ],
    }

    print("Input data:")
    print(json.dumps(test_data, indent=2))

    # Create and validate the model
    try:
        outer = Outer(**test_data)

        print("\nParsed model:")
        print(outer.model_dump())

        print("\nIndividual inners:")
        for i, inner in enumerate(outer.inners):
            print(f"  Inner {i}: a={inner.a}, b={inner.b}, c='{inner.c}'")

    except pydantic.ValidationError as e:
        print(f"Error: {e}")

    # Test with custom defaults suffix
    test_data_custom = {
        "items_base": {"b": 99},
        "items": [{"a": 10}, {"a": 20, "b": 30}],
    }

    # Test without defaults
    print("\n\nTesting without defaults:")
    no_defaults_data = {
        "inners": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
    }
    try:
        outer_no_defaults = Outer(**no_defaults_data)
        print(f"Result: {outer_no_defaults.model_dump()}")
    except pydantic.ValidationError as e:
        print(f"Error: {e}")

    # Test schema generation
    print("\n\nTesting JSON Schema:")
    schema = Outer.model_json_schema()
    print(json.dumps(schema, indent=2))

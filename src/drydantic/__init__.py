"""Tools for DRY (don't repeat yourself) inputs to pydantic models"""

from .defaults_merge_mixin import DefaultsMergeMixin, supports_defaults

__all__ = [
    "DefaultsMergeMixin",
    "supports_defaults",
]

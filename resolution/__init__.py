"""
resolution/__init__.py
----------------------
Public API for the resolution package.
"""

from resolution.resolver import resolve_entities
from resolution.merger import merge_graphs, apply_resolution

__all__ = ["resolve_entities", "merge_graphs", "apply_resolution"]

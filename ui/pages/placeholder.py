"""Placeholder for unimplemented pages."""
import flet as ft

from ..components.common import empty_state


def placeholder(icon, title: str, phase: str = "") -> ft.Control:
    return empty_state(icon, title, "This page is not available.")

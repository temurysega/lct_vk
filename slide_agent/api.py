"""Compatibility entry point for the existing CLI and integrations."""

from backend.main import app, health

__all__ = ["app", "health"]

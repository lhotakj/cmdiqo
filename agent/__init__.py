"""Cmdiqo Remote Agent package."""

try:
    from agent._version import __version__
except ImportError:
    __version__ = "1.0.0"

__all__ = ["__version__"]

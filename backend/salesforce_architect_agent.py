"""Shim: architect agent lives in services.architect_agent."""

from services.architect_agent import *  # noqa: F403
try:
    from services.architect_agent import __all__  # noqa: F401
except ImportError:
    pass

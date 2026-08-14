"""Role-scoped local MCP transport for 42 Ultracode."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .server import MCPServer, main, serve

__all__ = ["MCPServer", "main", "serve"]


def __getattr__(name: str) -> object:
    """Lazily expose server symbols without preloading the module entrypoint."""

    if name in __all__:
        from .server import MCPServer, main, serve

        return {"MCPServer": MCPServer, "main": main, "serve": serve}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

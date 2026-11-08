"""
Hermes Agent plugin entrypoint: Chronos.

Registers the speculative-branching tools and the session lifecycle hooks. Nothing is bound to a
directory here: each tool call resolves the calling session's working directory itself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# The plugin is a package with a nested ``chronos`` module; make it importable under any loader.
_plugin_dir = str(Path(__file__).resolve().parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from chronos.hooks import ChronosHooks
from chronos.manager import ChronosManager
from chronos.tools import get_tool_definitions


def register(ctx: Any) -> None:
    """Called by the Hermes plugin loader."""
    manager = ChronosManager(
        get_config=getattr(ctx, "get_config", None),
        session_cwd=getattr(ctx, "session_cwd", None),
        dispatch_tool=getattr(ctx, "dispatch_tool", None),
    )
    hooks = ChronosHooks(manager)

    for tool_name, (schema, handler) in get_tool_definitions(manager).items():
        ctx.register_tool(name=tool_name, toolset="chronos", schema=schema, handler=handler)

    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("on_session_finalize", hooks.on_session_finalize)

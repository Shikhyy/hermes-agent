"""
Hermes Agent Plugin Entrypoint: Chronos
Registers MCTS speculative branching tools, lifecycle hooks, and Hermes Desktop integration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure plugin directory is in sys.path for isolated capability probes and tests
_plugin_dir = str(Path(__file__).resolve().parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

from chronos.config import ChronosConfig
from chronos.engine import ChronosEngine
from chronos.hooks import ChronosHooks
from chronos.tools import get_tool_definitions


def register(ctx: Any) -> None:
    """
    Called by Hermes Agent runtime when loading the plugin.
    ctx provides workspace context, hook subscription, and tool registration APIs.
    """
    workspace_dir = getattr(ctx, "workspace_dir", None)
    if not workspace_dir:
        workspace_dir = getattr(ctx, "workspace_path", os.getcwd())

    config = ChronosConfig.load(workspace_dir)
    engine = ChronosEngine(workspace_root=workspace_dir, config=config)
    hooks = ChronosHooks(engine)

    # Attach engine to context for other plugins or direct inspection
    setattr(ctx, "chronos", engine)

    # 1. Register model tools
    tool_defs = get_tool_definitions(engine)
    for tool_name, (schema, handler) in tool_defs.items():
        ctx.register_tool(
            name=tool_name,
            toolset="chronos",
            schema=schema,
            handler=handler,
        )

    # 2. Register lifecycle hooks
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("on_session_end", hooks.on_session_end)

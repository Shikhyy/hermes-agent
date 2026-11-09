"""
File access for merges, routed through Hermes' own file tools.

A merge must not write the user's files behind Hermes' back: ``write_file`` applies core's write
denylist, ``HERMES_WRITE_SAFE_ROOT`` and write approvals. ``write_file`` also refuses to overwrite an
existing file the task has not fully read, so ``prepare`` does a complete ``read_file`` first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

_MAX_READ_PAGES = 200
_PAGE_LINES = 2000


class FileIOError(RuntimeError):
    """A Hermes file tool refused or failed; the file was not (fully) written."""


class HermesFileIO:
    """``read_file`` / ``write_file`` through ``ctx.dispatch_tool``, bound to one session's task ids."""

    def __init__(self, dispatch: Callable[..., str], **dispatch_kwargs: Any):
        self._dispatch = dispatch
        self._kw = dispatch_kwargs

    def _call(self, tool: str, args: dict) -> dict:
        raw = self._dispatch(tool, args, **self._kw)
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as exc:
            raise FileIOError(f"{tool} returned unparseable output for {args.get('path')}") from exc
        if not isinstance(data, dict):
            raise FileIOError(f"{tool} returned an unexpected result for {args.get('path')}")
        if data.get("error"):
            raise FileIOError(f"{tool} refused {args.get('path')}: {data['error']}")
        return data

    def prepare(self, path: Path) -> None:
        """Read an existing file completely so write_file's read-before-write guard accepts an overwrite."""
        offset = 1
        for _ in range(_MAX_READ_PAGES):
            data = self._call("read_file", {"path": str(path), "offset": offset, "limit": _PAGE_LINES})
            next_offset = data.get("next_offset")
            if not next_offset:
                return
            offset = int(next_offset)
        raise FileIOError(f"{path} is too large to read completely before writing")

    def write(self, path: Path, content: str) -> None:
        self._call("write_file", {"path": str(path), "content": content})

"""Fail-closed filesystem allowlist guard for Nothing command entry points.

Set ``MASK_TOOL_ALLOWED_DIRS`` (an ``os.pathsep``-separated list of directories)
to define which paths the masking command-line tools may read from or write to.
Every input/output path is resolved and must live inside one of the allowed
roots, otherwise a :class:`PermissionError` is raised.

Desktop-native commands use separately registered path capabilities. This
module has no caller-controlled unrestricted mode.

This module only depends on the standard library so it is safe to import from
the packaged (PyInstaller) engine and development scripts.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

ALLOWED_DIRS_ENV = "MASK_TOOL_ALLOWED_DIRS"


def resolve_allowed_roots(
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> list[Path]:
    """Return resolved allowlist roots.

    An empty list means no allowlist was configured and access fails closed.
    """
    raw = os.environ.get(env_var, "").strip()
    if raw:
        parts = [part for part in raw.split(os.pathsep) if part.strip()]
    elif default_roots:
        parts = [str(part) for part in default_roots]
    else:
        return []
    roots: list[Path] = []
    for part in parts:
        try:
            roots.append(Path(part).expanduser().resolve())
        except OSError:
            continue
    return roots


def is_path_allowed(
    path: str | os.PathLike[str],
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> bool:
    """Return ``True`` only for a path inside an allowed root."""
    roots = resolve_allowed_roots(default_roots, env_var)
    try:
        target = Path(path).expanduser().resolve()
    except OSError:
        return False
    for root in roots:
        if target == root or root in target.parents:
            return True
    return False


def require_allowed_path(
    path: str | os.PathLike[str],
    *,
    label: str = "path",
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> str:
    """Return ``str(path)`` when allowed, else raise ``PermissionError``."""
    if not is_path_allowed(path, default_roots=default_roots, env_var=env_var):
        raise PermissionError(f"{label} is outside {env_var}: {path}")
    return str(path)

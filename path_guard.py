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
        parts = [part.strip() for part in raw.split(os.pathsep) if part.strip()]
    elif default_roots:
        parts = [str(part) for part in default_roots]
    else:
        return []
    roots: list[Path] = []
    for part in parts:
        try:
            roots.append(_canonical_path(part))
        except (OSError, RuntimeError):
            continue
    return roots


def _canonical_path(path: str | os.PathLike[str]) -> Path:
    """Resolve a path using the host filesystem's comparison semantics."""
    resolved = Path(path).expanduser().resolve()
    # normcase is a no-op on POSIX and performs both separator and case
    # normalization on Windows.  The latter matters because Windows paths
    # are case-insensitive even when the spelling supplied by two callers
    # differs.
    return Path(os.path.normcase(os.fspath(resolved)))


def is_path_allowed(
    path: str | os.PathLike[str],
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> bool:
    """Return ``True`` only for a path inside an allowed root."""
    roots = resolve_allowed_roots(default_roots, env_var)
    try:
        target = _canonical_path(path)
    except (OSError, RuntimeError):
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

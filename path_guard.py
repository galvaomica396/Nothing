"""Opt-in filesystem allowlist guard for Nothing command entry points.

Set ``MASK_TOOL_ALLOWED_DIRS`` (an ``os.pathsep``-separated list of directories)
to restrict which paths the masking command-line tools may read from or write
to. Every input/output path is then resolved and must live inside one of the
allowed roots, otherwise a :class:`PermissionError` is raised.

Design goals
------------
* **Opt-in.** When the variable is unset *and* no explicit ``default_roots`` are
  supplied, :func:`is_path_allowed` returns ``True`` for every path. This keeps
  the Tauri desktop default flow (which does its own registration/canonicalization
  and never sets the variable) completely unaffected.
* **Single source of truth.** The ``scripts/`` command entry points share this
  module instead of each re-implementing the check. They pass no default roots,
  so they stay unrestricted unless the operator opts in.

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
) -> list[Path] | None:
    """Return the resolved allowlist roots, or ``None`` when unrestricted.

    ``None`` means "no restriction": the environment variable is unset/empty and
    the caller supplied no ``default_roots``.
    """
    raw = os.environ.get(env_var, "").strip()
    if raw:
        parts = [part for part in raw.split(os.pathsep) if part.strip()]
    elif default_roots:
        parts = [str(part) for part in default_roots]
    else:
        return None
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
    """Return ``True`` when ``path`` is inside the (opt-in) allowlist."""
    roots = resolve_allowed_roots(default_roots, env_var)
    if roots is None:
        return True
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
    """Return ``str(path)`` when allowed, else raise a clear ``PermissionError``.

    A no-op passthrough when the allowlist is not configured.
    """
    if not is_path_allowed(path, default_roots=default_roots, env_var=env_var):
        raise PermissionError(f"{label} is outside {env_var}: {path}")
    return str(path)

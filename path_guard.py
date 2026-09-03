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
    """Resolve a physical path while preserving its spelling for I/O.

    Windows' case-folding and verbatim-prefix rules are comparison concerns,
    not safe replacements for the path passed to filesystem APIs. In
    particular, retaining the resolved verbatim path keeps long-path I/O
    working; callers that need a comparison key must use ``_comparison_path``.
    """
    return Path(path).expanduser().resolve()


def _comparison_path(path: str | os.PathLike[str]) -> Path:
    """Normalize a path for equality checks without retaining Win32 prefixes."""
    value = os.fspath(path)
    if os.name == "nt":
        value = os.path.normcase(value)
        if value.startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def same_path(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    """Compare paths after applying the host's case and verbatim-prefix rules."""
    return _comparison_path(left) == _comparison_path(right)


def _reject_final_symlink(path: str | os.PathLike[str]) -> None:
    """Reject only the supplied path's final symlink component.

    Resolved parent aliases are safe to use because callers receive the
    canonical path below and must use that path for subsequent I/O. Rejecting
    every symlink in ``path.parents`` incorrectly blocks ordinary Windows
    junctions such as redirected user folders.
    """
    candidate = Path(path).expanduser()
    try:
        if candidate.is_symlink():
            raise PermissionError(f"path is a symlink: {path}")
    except OSError as error:
        raise PermissionError(f"path cannot be inspected: {path}") from error


def _guarded_canonical_path(
    path: str | os.PathLike[str],
    *,
    default_roots: Iterable[str] | None,
    env_var: str,
) -> Path:
    _reject_final_symlink(path)
    target = _canonical_path(path)
    roots = resolve_allowed_roots(default_roots, env_var)
    comparison_target = _comparison_path(target)
    if any(
        comparison_target == _comparison_path(root)
        or _comparison_path(root) in comparison_target.parents
        for root in roots
    ):
        return target
    raise PermissionError(f"path is outside {env_var}: {path}")


def is_path_allowed(
    path: str | os.PathLike[str],
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> bool:
    """Return ``True`` only for a path inside an allowed root."""
    try:
        _guarded_canonical_path(
            path,
            default_roots=default_roots,
            env_var=env_var,
        )
    except (OSError, RuntimeError):
        return False
    except PermissionError:
        return False
    return True


def require_allowed_path(
    path: str | os.PathLike[str],
    *,
    label: str = "path",
    default_roots: Iterable[str] | None = None,
    env_var: str = ALLOWED_DIRS_ENV,
) -> Path:
    """Return the canonical path when allowed, else raise ``PermissionError``."""
    try:
        return _guarded_canonical_path(
            path,
            default_roots=default_roots,
            env_var=env_var,
        )
    except (OSError, RuntimeError, PermissionError) as error:
        raise PermissionError(f"{label} is outside {env_var}: {path}") from error

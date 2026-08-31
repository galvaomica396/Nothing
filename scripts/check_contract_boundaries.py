from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_TYPESCRIPT = REPOSITORY_ROOT / "src/contracts/generated/analysisManifestV1.ts"
GENERATED_RUST = REPOSITORY_ROOT / "src-tauri/src/contracts_generated.rs"
SECURITY_MODULES = (
    REPOSITORY_ROOT / "src-tauri/src/path_security.rs",
    REPOSITORY_ROOT / "src-tauri/src/masking_run_session.rs",
)


def boundary_violations(
    *,
    typescript: str,
    rust: str,
    security_sources: Mapping[str, str],
) -> tuple[str, ...]:
    violations: list[str] = []
    for forbidden in ("import ", "require(", "../", "src/", "path_security", "masking_run_session"):
        if forbidden in typescript:
            violations.append(f"generated TypeScript DTO references forbidden module token: {forbidden}")
    for line in rust.splitlines():
        stripped = line.strip()
        if stripped.startswith("use ") and stripped != "use serde::{Deserialize, Serialize};":
            violations.append(f"generated Rust DTO imports outside serde: {stripped}")
        if any(token in stripped for token in ("crate::", "super::", "path_security", "masking_run_session")):
            violations.append(f"generated Rust DTO references forbidden module token: {stripped}")
    for module, source in security_sources.items():
        if "mod contracts_generated" in source:
            violations.append(f"security module owns generated DTO module: {module}")
        for line in source.splitlines():
            if "contracts_generated" in line and "crate::contracts_generated" not in line:
                violations.append(f"security module bypasses DTO import boundary: {module}")
    return tuple(violations)


def main() -> int:
    violations = boundary_violations(
        typescript=GENERATED_TYPESCRIPT.read_text(encoding="utf-8"),
        rust=GENERATED_RUST.read_text(encoding="utf-8"),
        security_sources={
            module.relative_to(REPOSITORY_ROOT).as_posix(): module.read_text(encoding="utf-8")
            for module in SECURITY_MODULES
        },
    )
    if violations:
        print("\n".join(violations))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final


TOP_LEVEL_TASK_RE: Final[re.Pattern[str]] = re.compile(
    r"^- \[(?P<mark>[ x])\] (?P<task_id>(?:\d+|F\d+))\. (?P<title>.+)$"
)
ABSOLUTE_HOME_PATH_RE: Final[re.Pattern[str]] = re.compile(r"/Users/[^/\s]+")
PRIVATE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    ABSOLUTE_HOME_PATH_RE,
    re.compile(r"010-[0-9]{4}-[0-9]{4}"),
    re.compile(r"[0-9]{6}-[0-9]{7}"),
    re.compile("API" + "_KEY"),
    re.compile("TOK" + "EN"),
    re.compile("COO" + "KIE"),
)
FINAL_WAVE_TASK_RE: Final[re.Pattern[str]] = re.compile(r"^F\d+$")


@dataclass(frozen=True, slots=True)
class TaskEntry:
    task_id: str
    title: str
    checked: bool
    line: int


@dataclass(frozen=True, slots=True)
class AuditError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit top-level plan checkboxes and evidence presence."
    )
    parser.add_argument("--plan", required=True, help="Markdown plan path.")
    parser.add_argument(
        "--evidence-root",
        default="build/plan-evidence",
        help="Evidence root directory. Defaults to build/plan-evidence.",
    )
    parser.add_argument("--output", required=True, help="Output JSON path.")
    return parser.parse_args()


def sanitize_path(path: Path, workspace_root: Path) -> str:
    resolved_path = path.resolve(strict=False)
    resolved_root = workspace_root.resolve(strict=False)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
        return relative_path.as_posix()
    except ValueError:
        return path.name


def sanitize_message(message: str, workspace_root: Path) -> str:
    workspace = workspace_root.as_posix()
    sanitized = message.replace(workspace, "<WORKSPACE>")
    return ABSOLUTE_HOME_PATH_RE.sub("<HOME>", sanitized)


def load_plan(plan_path: Path, workspace_root: Path) -> list[TaskEntry]:
    if not plan_path.is_file():
        raise AuditError(
            "missing_plan",
            f"missing plan: {sanitize_path(plan_path, workspace_root)}",
        )
    rows: list[TaskEntry] = []
    for line_no, line in enumerate(plan_path.read_text(encoding="utf-8").splitlines(), start=1):
        match = TOP_LEVEL_TASK_RE.match(line)
        if match is None:
            continue
        rows.append(
            TaskEntry(
                task_id=match.group("task_id"),
                title=match.group("title").strip(),
                checked=match.group("mark") == "x",
                line=line_no,
            )
        )
    if not rows:
        raise AuditError("no_tasks", "no top-level task checkboxes found")
    return rows


def matches_task_evidence(task_id: str, relative_path: str) -> bool:
    lowered_path = relative_path.lower()
    lowered_name = Path(relative_path).name.lower()
    if task_id.startswith("F") and lowered_name.startswith(f"{task_id.lower()}-"):
        return True
    token_re = re.compile(rf"(^|/)task-{re.escape(task_id.lower())}(?:[^0-9]|$)")
    return token_re.search(lowered_path) is not None


def collect_evidence_files(evidence_root: Path, workspace_root: Path) -> list[str]:
    if not evidence_root.is_dir():
        raise AuditError(
            "missing_evidence_root",
            f"missing evidence root: {sanitize_path(evidence_root, workspace_root)}",
        )
    files = [
        sanitize_path(path, workspace_root)
        for path in evidence_root.rglob("*")
        if path.is_file()
    ]
    files.sort()
    return files


def evidence_for_task(task_id: str, evidence_files: list[str]) -> list[str]:
    return [path for path in evidence_files if matches_task_evidence(task_id, path)]


def contains_private_data(value: object) -> bool:
    if isinstance(value, dict):
        return any(contains_private_data(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_private_data(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in PRIVATE_PATTERNS)
    return False


def is_final_wave_task(task: TaskEntry) -> bool:
    return FINAL_WAVE_TASK_RE.match(task.task_id) is not None


def build_blockers(
    unchecked_implementation_tasks: list[TaskEntry],
    missing_evidence: list[TaskEntry],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    if unchecked_implementation_tasks:
        blockers.append(
            {
                "code": "unchecked_implementation_tasks",
                "message": "Unchecked implementation plan items remain.",
                "task_ids": [task.task_id for task in unchecked_implementation_tasks],
            }
        )
    for task in missing_evidence:
        blockers.append(
            {
                "code": "missing_task_evidence",
                "message": f"Checked task {task.task_id} has no evidence artifact.",
                "task_id": task.task_id,
            }
        )
    return blockers


def build_report(
    plan_path: Path,
    evidence_root: Path,
    output_path: Path,
    workspace_root: Path,
    started_at: float,
) -> dict[str, object]:
    tasks = load_plan(plan_path, workspace_root)
    evidence_files = collect_evidence_files(evidence_root, workspace_root)

    implementation_tasks = [task for task in tasks if not is_final_wave_task(task)]
    final_wave_tasks = [task for task in tasks if is_final_wave_task(task)]
    top_level_unchecked_tasks = [task for task in tasks if not task.checked]
    unchecked_implementation_tasks = [
        task for task in implementation_tasks if not task.checked
    ]
    pending_final_wave_tasks = [task for task in final_wave_tasks if not task.checked]
    missing_evidence = [
        task
        for task in implementation_tasks
        if task.checked and not evidence_for_task(task.task_id, evidence_files)
    ]
    blockers = build_blockers(unchecked_implementation_tasks, missing_evidence)

    task_rows: list[dict[str, object]] = []
    for task in tasks:
        task_evidence = evidence_for_task(task.task_id, evidence_files)
        task_rows.append(
            {
                **asdict(task),
                "evidence_present": bool(task_evidence),
                "evidence_count": len(task_evidence),
                "evidence_status": "present" if task_evidence else "missing",
                "evidence_files": task_evidence,
            }
        )

    report = {
        "ok": not blockers,
        "pass": not blockers,
        "plan": sanitize_path(plan_path, workspace_root),
        "evidence_root": sanitize_path(evidence_root, workspace_root),
        "output": sanitize_path(output_path, workspace_root),
        "top_level_total": len(tasks),
        "checked_count": sum(1 for task in tasks if task.checked),
        "top_level_checked_count": sum(1 for task in tasks if task.checked),
        "top_level_unchecked_count": len(top_level_unchecked_tasks),
        "top_level_unchecked_task_ids": [
            task.task_id for task in top_level_unchecked_tasks
        ],
        "unchecked_count": len(unchecked_implementation_tasks),
        "unchecked_count_scope": "implementation",
        "checked_task_ids": [task.task_id for task in tasks if task.checked],
        "unchecked_task_ids": [
            task.task_id for task in unchecked_implementation_tasks
        ],
        "unchecked_task_ids_scope": "implementation",
        "implementation_task_ids": [task.task_id for task in implementation_tasks],
        "implementation_total": len(implementation_tasks),
        "implementation_checked_count": sum(
            1 for task in implementation_tasks if task.checked
        ),
        "implementation_unchecked_count": len(unchecked_implementation_tasks),
        "implementation_unchecked_task_ids": [
            task.task_id for task in unchecked_implementation_tasks
        ],
        "checked_implementation_task_ids": [
            task.task_id for task in implementation_tasks if task.checked
        ],
        "unchecked_implementation_task_ids": [
            task.task_id for task in unchecked_implementation_tasks
        ],
        "final_wave_task_ids": [task.task_id for task in final_wave_tasks],
        "pending_final_wave_task_ids": [
            task.task_id for task in pending_final_wave_tasks
        ],
        "blockers": blockers,
        "tasks": task_rows,
        "evidence": evidence_files,
        "generated_duration_ms": int((time.time() - started_at) * 1000),
    }
    if contains_private_data(report):
        raise AuditError("private_data", "report contains private-looking data")
    return report


def emit_structured_error(code: str, message: str, workspace_root: Path) -> int:
    safe_message = sanitize_message(message, workspace_root)
    sys.stderr.write(
        json.dumps(
            {
                "ok": False,
                "pass": False,
                "error": {
                    "code": code,
                    "message": safe_message,
                },
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 2


def main() -> int:
    started_at = time.time()
    args = parse_args()
    workspace_root = Path.cwd().resolve()
    plan_path = Path(args.plan)
    evidence_root = Path(args.evidence_root)
    output_path = Path(args.output)

    try:
        report = build_report(
            plan_path=plan_path,
            evidence_root=evidence_root,
            output_path=output_path,
            workspace_root=workspace_root,
            started_at=started_at,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except AuditError as exc:
        return emit_structured_error(exc.code, exc.message, workspace_root)
    except OSError as exc:
        return emit_structured_error("io_error", str(exc), workspace_root)

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "pass": report["pass"],
                "top_level_total": report["top_level_total"],
                "top_level_unchecked_count": report["top_level_unchecked_count"],
                "top_level_unchecked_task_ids": report["top_level_unchecked_task_ids"],
                "implementation_unchecked_count": report[
                    "implementation_unchecked_count"
                ],
                "implementation_unchecked_task_ids": report[
                    "implementation_unchecked_task_ids"
                ],
                "unchecked_count": report["unchecked_count"],
                "unchecked_count_scope": report["unchecked_count_scope"],
                "pending_final_wave_task_ids": report["pending_final_wave_task_ids"],
                "blocker_count": len(report["blockers"]),
                "output": report["output"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

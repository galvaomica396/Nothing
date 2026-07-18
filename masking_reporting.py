#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reporting subsystem extracted from document_masker_ocr_gui.

Behavior-preserving move of the local-LLM refinement pipeline, the logging
sanitizer, and the quality-gate helpers. Pure code movement; the safe-report
builder itself stays in the facade because it owns option/version coupling.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMCandidateOccurrence:
    start: int
    end: int
    replacement: str
    context: str


@dataclass(frozen=True)
class LLMCandidate:
    tag: str
    label: str
    value: str
    normalized_value: str
    occurrences: tuple[LLMCandidateOccurrence, ...]
    score: int


def _llm_bin_exists(cmd: str) -> bool:
    if not cmd.strip():
        return False
    argv = shlex.split(cmd)
    if not argv:
        return False
    return shutil.which(argv[0]) is not None


def _normalize_llm_candidate_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip(" ,;:/"))


def _score_llm_candidate(tag: str, value: str, frequency: int) -> int:
    context_weight = {
        "LEGAL_PARTY": 90,
        "APPROVAL_LINE": 70,
        "COMPANY": 50,
    }.get(tag, 0)

    compact = re.sub(r"\s+", "", value)
    length = len(compact)
    if tag in {"LEGAL_PARTY", "APPROVAL_LINE"}:
        if 2 <= length <= 4:
            length_weight = 28
        elif 5 <= length <= 6:
            length_weight = 18
        else:
            length_weight = 8
    else:
        if 3 <= length <= 12:
            length_weight = 24
        elif 13 <= length <= 24:
            length_weight = 14
        else:
            length_weight = 6

    frequency_weight = min(frequency, 5) * 12
    return context_weight + length_weight + frequency_weight


def _collect_llm_candidates(masked_text: str) -> list[LLMCandidate]:
    specs: list[tuple[re.Pattern[str], str, str]] = [
        (re.compile(r"(?P<label>\b(?:원고|피고|신청인|피신청인|청구인|피청구인|항고인|피항고인)\b\s+)(?P<value>[가-힣]{2,6})(?=[^가-힣]|$)"), "LEGAL_PARTY", "당사자명"),
        (re.compile(r"(?P<label>\b(?:기안자|검토자|협조자|결재자|담당자|주무관|팀장|과장|국장)\b\s+)(?P<value>[가-힣]{2,6})(?=[^가-힣]|$)"), "APPROVAL_LINE", "결재/담당자명"),
        (re.compile(r"(?P<label>\b(?:회사명|법인명|상호|기관명)\b\s*[:：]?\s*)(?P<value>[^\n\[\]]{2,40})"), "COMPANY", "기관/법인"),
    ]

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for pat, tag, label in specs:
        for m in pat.finditer(masked_text):
            candidate = _normalize_llm_candidate_value(m.group("value"))
            if not candidate or candidate.startswith("[") or candidate.endswith("]"):
                continue

            key = (tag, candidate)
            occurrence = LLMCandidateOccurrence(
                start=m.start(),
                end=m.end(),
                replacement=f"{m.group('label')}[{tag}]",
                context=m.group(0),
            )
            bucket = grouped.setdefault(
                key,
                {
                    "tag": tag,
                    "label": label,
                    "value": candidate,
                    "occurrences": [],
                },
            )
            bucket["occurrences"].append(occurrence)

    candidates: list[LLMCandidate] = []
    for bucket in grouped.values():
        occurrences = tuple(bucket["occurrences"])
        candidates.append(
            LLMCandidate(
                tag=bucket["tag"],
                label=bucket["label"],
                value=bucket["value"],
                normalized_value=bucket["value"],
                occurrences=occurrences,
                score=_score_llm_candidate(bucket["tag"], bucket["value"], len(occurrences)),
            )
        )

    candidates.sort(
        key=lambda item: (
            -item.score,
            -len(item.occurrences),
            item.occurrences[0].start,
            item.value,
        )
    )
    return candidates


def _apply_llm_candidate_replacements(text: str, occurrences: list[LLMCandidateOccurrence]) -> str:
    if not occurrences:
        return text

    out: list[str] = []
    cursor = 0
    for occ in sorted(occurrences, key=lambda item: (item.start, item.end)):
        if occ.start < cursor:
            continue
        out.append(text[cursor:occ.start])
        out.append(occ.replacement)
        cursor = occ.end
    out.append(text[cursor:])
    return "".join(out)


LLM_DECISION_LINE_PAT = re.compile(
    r"(?im)^\s*(?:FINAL\s+ANSWER|ANSWER|RESULT|DECISION|판정|결론)?\s*[:\-]?\s*(YES|NO)\b"
)
LLM_DECISION_TOKEN_PAT = re.compile(r"\b(YES|NO)\b")


def _parse_llm_yes_no_output(output: str) -> bool:
    out_upper = output.upper()

    line_match = LLM_DECISION_LINE_PAT.search(out_upper)
    if line_match:
        return line_match.group(1) == "YES"

    token_match = LLM_DECISION_TOKEN_PAT.search(out_upper)
    if token_match:
        return token_match.group(1) == "YES"

    raise ValueError("llm_yes_no_unparseable")


def _llm_yes_no_decision(cmd: str, model_path: str, label: str, value: str, context: str = "") -> bool:
    prompt = (
        "다음 값이 한국 행정/소송 문서에서 개인정보 또는 식별 메타데이터로 마스킹 대상인지 판단하세요.\n"
        f"라벨: {label}\n"
        f"값: {value}\n"
        f"문맥: {context}\n\n"
        "규칙:\n"
        "- 사람 이름, 법인명, 사건명, 법원명, 문서번호/공문번호 성격이면 YES\n"
        "- 일반 절차/지침/업무 용어면 NO\n"
        "출력은 반드시 한 단어로만: YES 또는 NO"
    )

    argv = shlex.split(cmd)
    full_cmd = argv + ["-m", model_path, "-ngl", "0", "-c", "1024", "-n", "8", "--temp", "0", "-p", prompt]
    p = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return _parse_llm_yes_no_output(out)


def _llm_failure_reason(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, ValueError):
        return "unparseable_response"
    if isinstance(exc, FileNotFoundError):
        return "llm_command_not_found"
    return "runtime_error"


def llm_refine_masking(masked_text: str, opts: dict[str, Any], counts: dict[str, int]) -> tuple[str, dict[str, Any]]:
    use_llm = bool(opts.get("local_llm_refine", False))
    llm_cmd = str(opts.get("local_llm_cmd", "llama-cli")).strip()
    llm_model = str(opts.get("local_llm_model", "")).strip()
    max_calls = max(int(opts.get("local_llm_max_calls", 25)), 0)

    meta = {
        "enabled": use_llm,
        "applied": False,
        "skipped_reason": None,
        "calls": 0,
        "accepted": 0,
        "rejected": 0,
        "total_candidates": 0,
        "selected_candidates": 0,
    }
    if not use_llm:
        meta["skipped_reason"] = "disabled"
        return masked_text, meta
    if not llm_model:
        meta["skipped_reason"] = "model_path_missing"
        return masked_text, meta
    if not _llm_bin_exists(llm_cmd):
        meta["skipped_reason"] = "llm_command_not_found"
        return masked_text, meta

    candidates = _collect_llm_candidates(masked_text)
    meta["total_candidates"] = len(candidates)
    selected = candidates[:max_calls]
    meta["selected_candidates"] = len(selected)

    accepted_occurrences: list[LLMCandidateOccurrence] = []
    for candidate in selected:
        meta["calls"] += 1
        try:
            ok = _llm_yes_no_decision(
                llm_cmd,
                llm_model,
                candidate.label,
                candidate.value,
                context=candidate.occurrences[0].context,
            )
        except Exception as exc:
            if not meta["skipped_reason"]:
                meta["skipped_reason"] = _llm_failure_reason(exc)
            meta["rejected"] += 1
            continue
        if ok:
            meta["accepted"] += 1
            counts[candidate.tag] = counts.get(candidate.tag, 0) + len(candidate.occurrences)
            accepted_occurrences.extend(candidate.occurrences)
            continue
        meta["rejected"] += 1

    text = _apply_llm_candidate_replacements(masked_text, accepted_occurrences)
    meta["applied"] = meta["accepted"] > 0
    return text, meta


def sanitize_for_logging(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_for_logging(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_logging(v) for v in obj]
    if isinstance(obj, str):
        # 민감 원문 노출 방지: preview 류는 마스킹
        return re.sub(r"[가-힣A-Za-z0-9_]+:[^,\]\}\n]{2,80}", "[REDACTED_PREVIEW]", obj)
    return obj


NATIVE_REDACTION_FAILED_REASON_CODE = "native_redaction_failed"


def classify_redaction_failure_reason_code(exc: BaseException) -> str:
    """예외를 안전 리포트에 실을 사유 코드로 변환한다(C-5/E2-2).

    ``masking_redaction.ScannedPdfRedactionError`` 처럼 구조화된 ``reason_code``
    속성을 가진 예외는 그 값을 그대로 쓰고, 그 외 일반 실패는
    ``NATIVE_REDACTION_FAILED_REASON_CODE`` 로 뭉뚱그린다. Rust/TS 계약은
    바뀌지 않는다 — 여기서 만든 코드는 Python 쪽 safe report 전용 필드다.
    """
    code = getattr(exc, "reason_code", None)
    if isinstance(code, str) and code:
        return code
    return NATIVE_REDACTION_FAILED_REASON_CODE


def evaluate_quality_gate(pdf_redaction_result: dict[str, Any]) -> bool:
    verification = pdf_redaction_result.get("verification", {})
    targets_requested = int(pdf_redaction_result.get("targets_requested", 0) or 0)
    targets_hit = int(pdf_redaction_result.get("targets_hit", 0) or 0)
    missing_targets_count = int(
        pdf_redaction_result.get(
            "missing_targets_count",
            max(targets_requested - targets_hit, 0),
        )
        or 0
    )
    residual_hits = int(verification.get("residual_hits", 0) or 0)
    residual_fuzzy_hits = int(verification.get("residual_fuzzy_hits", 0) or 0)
    return (
        bool(verification.get("verified", False))
        and targets_requested > 0
        and targets_hit == targets_requested
        and missing_targets_count == 0
        and residual_hits == 0
        and residual_fuzzy_hits == 0
    )


def _safe_pdf_redaction_summary(pdf_redaction_result: dict[str, Any]) -> dict[str, Any]:
    verification = pdf_redaction_result.get("verification", {})
    targets_requested = int(pdf_redaction_result.get("targets_requested", 0) or 0)
    targets_hit = int(pdf_redaction_result.get("targets_hit", 0) or 0)
    missing_targets_count = int(
        pdf_redaction_result.get("missing_targets_count", max(targets_requested - targets_hit, 0)) or 0
    )
    return {
        "enabled": bool(pdf_redaction_result.get("enabled", False)),
        "status": pdf_redaction_result.get("status", "skipped"),
        "output_file": None,
        "display_mode": pdf_redaction_result.get("display_mode", "black"),
        "targets_requested": targets_requested,
        "targets_hit": targets_hit,
        "missing_targets_count": missing_targets_count,
        "annotations_added": int(pdf_redaction_result.get("annotations_added", 0) or 0),
        "rects_from_word_fallback": int(pdf_redaction_result.get("rects_from_word_fallback", 0) or 0),
        "excluded_hits": int(pdf_redaction_result.get("excluded_hits", 0) or 0),
        "excluded_regions": int(pdf_redaction_result.get("excluded_regions", 0) or 0),
        "review_items": safe_review_item_summaries(pdf_redaction_result.get("review_items", [])),
        "reason_code": safe_reason_code(pdf_redaction_result.get("reason_code")),
        "verification": {
            "verified": bool(verification.get("verified", False)),
            "residual_hits": int(verification.get("residual_hits", 0) or 0),
            "residual_fuzzy_hits": int(verification.get("residual_fuzzy_hits", 0) or 0),
            "reason_code": safe_reason_code(
                verification.get("reason_code") or pdf_redaction_result.get("reason_code")
            ),
        },
    }


def safe_reason_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if re.fullmatch(r"[a-z0-9_]{1,80}", normalized):
        return normalized
    return NATIVE_REDACTION_FAILED_REASON_CODE


def safe_review_item_summaries(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "MANUAL")
        if not re.fullmatch(r"[A-Z0-9_]{1,40}", tag):
            tag = "MANUAL"
        status = str(item.get("status") or "needs_review")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", status):
            status = "needs_review"
        count = item.get("count", 1)
        try:
            count = max(int(count or 0), 0)
        except (TypeError, ValueError):
            count = 0
        summaries.append(
            {
                "tag": tag,
                "display_token": f"[{tag}]",
                "status": status,
                "count": count,
                "raw_value_saved": False,
            }
        )
    return summaries


def enforce_quality_gate_or_raise(report: dict[str, Any]) -> None:
    checks = report.get("product_checks", {})
    if checks.get("quality_gate_passed", False):
        return
    missing = int(report.get("document_redaction", {}).get("missing_targets_count", 0) or 0)
    residual = int(report.get("document_redaction", {}).get("verification", {}).get("residual_hits", 0) or 0)
    raise RuntimeError(f"QUALITY_GATE_BLOCK: missing={missing}, residual={residual}")

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


LEGACY_WEAK_PLACE_NAMES = {"중동", "상동", "목동", "우동", "연동"}


def unique_sorted(values: list[str]) -> list[str]:
    return sorted({v.strip() for v in values if v and v.strip()}, key=lambda x: (len(x), x))


def normalize_field(value: str) -> str:
    return "".join(str(value).replace("\ufeff", "").split()).strip()


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig-replace"


def detect_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if "\t" in first_line:
        return "\t"
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
    except csv.Error:
        return ","


def strip_sido(full_name: str, sido_names: list[str]) -> tuple[str | None, str]:
    normalized = " ".join(full_name.split())
    for sido in sorted(sido_names, key=len, reverse=True):
        if normalized == sido:
            return sido, ""
        prefix = f"{sido} "
        if normalized.startswith(prefix):
            return sido, normalized[len(prefix) :].strip()
    return None, normalized


def is_current_row(status: str) -> bool:
    normalized = status.strip().casefold()
    if not normalized:
        return True
    return normalized in {"존재", "현존", "0", "n", "no", "false", "active"}


def legal_code_level(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 10:
        return "unknown"
    if digits[2:] == "00000000":
        return "sido"
    if digits[5:] == "00000":
        return "sigungu"
    if digits[8:] == "00":
        return "eupmyeondong"
    return "ri"


def looks_like_sido_name(name: str) -> bool:
    normalized = " ".join(name.split())
    if " " in normalized:
        return False
    return normalized.endswith(("특별시", "광역시", "특별자치시", "특별자치도", "자치도", "도"))


def build_region_seed(
    csv_path: Path,
    *,
    source_name: str | None = None,
    source_url: str | None = None,
    downloaded_at: str | None = None,
    include_disused: bool = False,
) -> dict[str, object]:
    sido: list[str] = []
    sigungu: list[str] = []
    eupmyeondong: list[str] = []
    ri: list[str] = []
    weak_place_names: list[str] = []
    single_tier_sido: list[str] = []
    record_count = 0
    skipped_disused_count = 0

    text, encoding = read_text_with_fallback(csv_path)
    delimiter = detect_delimiter(text)
    with io.StringIO(text) as f:
        dict_reader = csv.DictReader(f, delimiter=delimiter)
        normalized_fields = {normalize_field(name): name for name in (dict_reader.fieldnames or [])}
        rows = list(dict_reader)

    def field(*names: str) -> str | None:
        for name in names:
            normalized = normalize_field(name)
            if normalized in normalized_fields:
                return normalized_fields[normalized]
        return None

    legal_code_col = field("법정동코드")
    legal_name_col = field("법정동명")
    disuse_col = field("폐지여부", "폐지구분", "disuseAt")

    if legal_code_col and legal_name_col:
        current_rows: list[dict[str, str]] = []
        for row in rows:
            status = row.get(disuse_col, "") if disuse_col else ""
            if not include_disused and not is_current_row(status):
                skipped_disused_count += 1
                continue
            current_rows.append(row)

        sido = [
            " ".join(str(row[legal_name_col]).split())
            for row in current_rows
            if row.get(legal_name_col)
            and (
                legal_code_level(row.get(legal_code_col, "")) == "sido"
                or looks_like_sido_name(str(row[legal_name_col]))
            )
        ]
        known_sido = unique_sorted(sido)
        local_name_counts: Counter[str] = Counter()

        for row in current_rows:
            full_name = " ".join(str(row.get(legal_name_col, "")).split())
            if not full_name:
                continue
            record_count += 1
            _, tail = strip_sido(full_name, known_sido)
            parts = tail.split()
            level = legal_code_level(row.get(legal_code_col, ""))

            if level == "sigungu" and tail:
                sigungu.append(tail)
                sigungu.append(parts[-1])
            elif level == "eupmyeondong" and parts:
                eupmyeondong.append(parts[-1])
                local_name_counts[parts[-1]] += 1
                if len(parts) == 1:
                    owner_sido, _ = strip_sido(full_name, known_sido)
                    if owner_sido:
                        single_tier_sido.append(owner_sido)
            elif level == "ri" and parts:
                ri.append(parts[-1])
                local_name_counts[parts[-1]] += 1
    else:
        record_count = len(rows)
        sido_col = field("시도명", "시도", "sido")
        sigungu_col = field("시군구명", "시군구", "sigungu")
        eup_col = field("읍면동명", "읍면동", "법정동명", "eupmyeondong")
        ri_col = field("리명", "리", "ri")

        for row in rows:
            if sido_col and row.get(sido_col):
                sido.append(row[sido_col])
            if sigungu_col and row.get(sigungu_col):
                sigungu.append(row[sigungu_col])
            if eup_col and row.get(eup_col):
                eupmyeondong.append(row[eup_col])
            if ri_col and row.get(ri_col):
                ri.append(row[ri_col])

        local_name_counts = Counter(eupmyeondong + ri)

    for name, count in local_name_counts.items():
        compact = name.strip()
        if not (compact.endswith("동") or compact.endswith("리")):
            continue
        if len(compact) == 3:
            # 3-char 동/리 are the only tier the masking detector actually
            # consumes for weak-place matching (masking_rules._weak_place_patterns
            # keeps terms with len > 2). Gating them on count > 1 dropped every
            # legal name that appears exactly once nationwide, leaving detection
            # blind spots for genuine single-occurrence 동/리 (review L4). We now
            # include all 3-char 동/리 regardless of occurrence count.
            #
            # False-positive risk is bounded, so this expansion is deliberate:
            #   - the detector anchors matches on non-Hangul boundaries
            #     ((?<![가-힣])...(?![가-힣])), so a name only fires when it stands
            #     alone as a complete token, not as a substring of a larger word;
            #   - WEAK_PLACE is a REVIEW_TAG (privacy_spans.REVIEW_TAGS), so hits
            #     go to human review, not silent redaction — so the handful of
            #     3-char legal names that collide with common nouns (e.g.
            #     고사리/대가리, already present under the old count > 1 gate)
            #     surface for confirmation rather than mangling text.
            # This matches the project rule "마스킹 확대(과탐 소폭 증가) 허용,
            # 마스킹 축소 금지".
            weak_place_names.append(compact)
        elif len(compact) == 2:
            # 2-char names are not consumed by the detector (len > 2 filter) but
            # are retained under the historical count/legacy gate to keep the
            # generated JSON stable and avoid bloating it with unused entries.
            if count > 1 or compact in LEGACY_WEAK_PLACE_NAMES:
                weak_place_names.append(compact)

    source = source_name or f"local-file:{csv_path.name}"
    payload: dict[str, object] = {
        "schema_version": 2,
        "is_seed": False,
        "source": source,
        "source_file": csv_path.name,
        "source_file_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "source_encoding": encoding,
        "source_delimiter": "tab" if delimiter == "\t" else delimiter,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "record_count": record_count,
        "skipped_disused_count": skipped_disused_count,
        "sido": unique_sorted(sido),
        "sigungu": unique_sorted(sigungu),
        "eupmyeondong": unique_sorted(eupmyeondong),
        "ri": unique_sorted(ri),
        "single_tier_sido": unique_sorted(single_tier_sido),
        "weak_place_names": unique_sorted(weak_place_names),
    }
    if source_url:
        payload["source_url"] = source_url
    if downloaded_at:
        payload["downloaded_at"] = downloaded_at
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build data/kr_regions.json from a local Korean administrative-code CSV/TSV/TXT")
    parser.add_argument("--csv", required=True, help="Local CSV/TSV/TXT path. No network download is performed.")
    parser.add_argument("--out", default="data/kr_regions.json")
    parser.add_argument("--source-name", help="Human-readable source label stored in the generated JSON.")
    parser.add_argument("--source-url", help="Official source URL stored in the generated JSON.")
    parser.add_argument("--downloaded-at", help="Download date/time stored in the generated JSON, for example 2026-05-30.")
    parser.add_argument("--include-disused", action="store_true", help="Include rows marked as 폐지/closed.")
    args = parser.parse_args()

    payload = build_region_seed(
        Path(args.csv),
        source_name=args.source_name,
        source_url=args.source_url,
        downloaded_at=args.downloaded_at,
        include_disused=args.include_disused,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

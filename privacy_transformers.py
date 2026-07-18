"""De-identification transforms for detected PII tokens.

Three policies convert a masked token (``[NAME]``, ``[PHONE]``, ...) back into a
display value with a chosen residual-disclosure level:

* ``token`` (default) — keep the full ``[TAG]`` placeholder; nothing is disclosed.
* ``partial`` — intentionally reveal part of the original (phone last 4 digits,
  surname / 성씨, 시·도 region, business-number last 5, card last 4, ...) for
  readability. This exposes quasi-identifiers and carries linkage-attack risk.
* ``pseudonym`` — replace with a deterministic fake value; the same original
  always maps to the same pseudonym (connectivity preserved).

See ``docs/DEIDENTIFICATION_POLICY.md`` for the exact per-tag exposure table and
quasi-identifier risk notes. This module documents behavior only; changing the
exposure here must be mirrored in that document.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Literal


DeidentificationPolicy = Literal["token", "partial", "pseudonym"]
TOKEN_PAT: Final = re.compile(r"\[(?P<tag>[A-Z_]+)\]")
POLICIES: Final = {"token", "partial", "pseudonym"}
NAME_POOL: Final = ("김민준", "이서연", "박지훈", "최하은", "정도윤", "강서윤", "조현우", "윤지아")
FIRM_POOL: Final = ("한빛", "대정", "새길", "온율", "바른길", "해온")


@dataclass
class TransformState:
    pseudonyms: dict[tuple[str, str], str] = field(default_factory=dict)


class RedactionLike:
    tag: str
    text: str


def normalize_deidentification_policy(value: object) -> DeidentificationPolicy:
    return value if isinstance(value, str) and value in POLICIES else "token"


def _stable_index(tag: str, value: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{tag}\0{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value)


def _partial_phone(value: str) -> str:
    digits = _digits(value)
    if len(digits) >= 10:
        return f"{digits[:3]}-****-{digits[-4:]}"
    return "[PHONE]"


def _partial_email(value: str) -> str:
    local, sep, domain = value.partition("@")
    if not sep or not local or not domain:
        return "[EMAIL]"
    return f"{local[:1]}***@{domain}"


def _partial_name(value: str) -> str:
    compact = re.sub(r"\s+", "", value.strip())
    if not compact:
        return "[NAME]"
    return compact[:1] + "OO"


def _partial_address(value: str, tag: str) -> str:
    match = re.search(r"(.{2,30}?(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구))", value)
    if match:
        return f"{match.group(1)} [{tag}_DETAIL]"
    return f"[{tag}]"


def _partial_firm(value: str, tag: str) -> str:
    compact = re.sub(r"\s+", "", value.strip())
    if len(compact) >= 2:
        return compact[:1] + "**"
    return f"[{tag}]"


def _pseudonym_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized)


def _used_pseudonym_identities(key_tag: str, state: TransformState) -> set[str]:
    return {
        _pseudonym_identity(pseudonym)
        for (mapped_tag, _), pseudonym in state.pseudonyms.items()
        if mapped_tag == key_tag
    }


def _pool_pseudonym(
    pool: tuple[str, ...],
    preferred_index: int,
    original: str,
    key_tag: str,
    state: TransformState,
) -> str:
    original_identity = _pseudonym_identity(original)
    used = _used_pseudonym_identities(key_tag, state)
    for offset in range(len(pool)):
        candidate = pool[(preferred_index + offset) % len(pool)]
        identity = _pseudonym_identity(candidate)
        if identity != original_identity and identity not in used:
            return candidate

    suffix = 2
    while True:
        for offset in range(len(pool)):
            candidate = f"{pool[(preferred_index + offset) % len(pool)]}{suffix}"
            identity = _pseudonym_identity(candidate)
            if identity != original_identity and identity not in used:
                return candidate
        suffix += 1


def _generated_pseudonym(
    original: str,
    key_tag: str,
    state: TransformState,
    candidate_for_attempt: Callable[[int], str],
) -> str:
    original_identity = _pseudonym_identity(original)
    used = _used_pseudonym_identities(key_tag, state)
    attempt = 0
    while True:
        candidate = candidate_for_attempt(attempt)
        identity = _pseudonym_identity(candidate)
        if identity != original_identity and identity not in used:
            return candidate
        attempt += 1


def partial_value(tag: str, value: str) -> str:
    """Return a partially-masked display value that keeps a residual fragment.

    WARNING — this policy deliberately discloses quasi-identifiers:
    ``PHONE`` keeps the 3-digit prefix + last 4 digits, ``NAME`` keeps the
    surname (first character), ``ADDRESS``/``REGION`` keep down to the 시·도
    level, ``BUSINESS_REG_NO`` keeps the last 5 digits, ``CARD`` the last 4,
    and ``COMPANY``/``LAW_FIRM``/``COURT`` the first character. When the value is
    too short for its rule, it falls back to full ``[TAG]`` masking. Use only for
    internal review; prefer ``token`` for external release. See
    ``docs/DEIDENTIFICATION_POLICY.md``.
    """
    if tag == "PHONE":
        return _partial_phone(value)
    if tag == "EMAIL":
        return _partial_email(value)
    if tag in {"NAME", "LEGAL_PARTY", "APPROVAL_LINE", "ATTORNEY"}:
        return _partial_name(value)
    if tag in {"ADDRESS", "REGION"}:
        return _partial_address(value, tag)
    if tag == "BUSINESS_REG_NO":
        digits = _digits(value)
        return f"***-**-{digits[-5:]}" if len(digits) >= 5 else "[BUSINESS_REG_NO]"
    if tag == "CARD":
        digits = _digits(value)
        return f"****-****-****-{digits[-4:]}" if len(digits) >= 4 else "[CARD]"
    if tag in {"COMPANY", "LAW_FIRM", "COURT"}:
        return _partial_firm(value, tag)
    return f"[{tag}]"


def pseudonym_value(tag: str, value: str, state: TransformState) -> str:
    """Return a deterministic fake value for ``value`` under the given ``tag``.

    The mapping is stable: the same original always yields the same pseudonym
    (via a sha256-derived index into fixed pools), and repeats within a run reuse
    the cached value in ``state``. This preserves connectivity — the frequency
    and co-occurrence of an entity survive — so beware frequency-based inference
    even though the raw value is hidden. See ``docs/DEIDENTIFICATION_POLICY.md``.
    """
    key_tag = "PERSON" if tag in {"NAME", "LEGAL_PARTY", "APPROVAL_LINE", "ATTORNEY"} else tag
    key = (key_tag, value)
    if key in state.pseudonyms:
        cached = state.pseudonyms[key]
        if _pseudonym_identity(cached) != _pseudonym_identity(value):
            return cached
        del state.pseudonyms[key]
    idx = _stable_index(tag, value, 9000) + 1000
    if tag in {"NAME", "LEGAL_PARTY", "APPROVAL_LINE", "ATTORNEY"}:
        pseudo = _pool_pseudonym(
            NAME_POOL,
            _stable_index(tag, value, len(NAME_POOL)),
            value,
            key_tag,
            state,
        )
    elif tag == "PHONE":
        def phone_candidate(attempt: int) -> str:
            sequence = idx - 1000 + attempt
            return f"010-{sequence // 9000:04d}-{sequence % 9000 + 1000:04d}"

        pseudo = _generated_pseudonym(value, key_tag, state, phone_candidate)
    elif tag == "EMAIL":
        pseudo = _generated_pseudonym(
            value,
            key_tag,
            state,
            lambda attempt: f"user{idx + attempt}@example.invalid",
        )
    elif tag in {"ADDRESS", "REGION"}:
        address_index = idx % 300 + 1
        pseudo = _generated_pseudonym(
            value,
            key_tag,
            state,
            lambda attempt: f"서울특별시 중구 샘플로 {address_index + attempt}",
        )
    elif tag == "LAW_FIRM":
        pseudo = _pool_pseudonym(
            FIRM_POOL,
            _stable_index(tag, value, len(FIRM_POOL)),
            value,
            key_tag,
            state,
        )
    elif tag == "COMPANY":
        company_index = idx % 100
        pseudo = _generated_pseudonym(
            value,
            key_tag,
            state,
            lambda attempt: f"주식회사 샘플{company_index + attempt}",
        )
    else:
        pseudo = _generated_pseudonym(
            value,
            key_tag,
            state,
            lambda attempt: f"[{tag}_{idx + attempt}]",
        )
    state.pseudonyms[key] = pseudo
    return pseudo


def transform_value(tag: str, value: str, policy: DeidentificationPolicy, state: TransformState) -> str:
    if policy == "partial":
        return partial_value(tag, value)
    if policy == "pseudonym":
        return pseudonym_value(tag, value, state)
    return f"[{tag}]"


def apply_deidentification_policy(
    masked_text: str,
    matches: Iterable[RedactionLike],
    policy: object,
    *,
    korean_tokens: bool = False,
    state: TransformState | None = None,
) -> str:
    """Rewrite ``[TAG]`` placeholders in already-masked text per ``policy``.

    ``matches`` supplies the original values in detection order, bucketed by tag,
    so each successive ``[TAG]`` token is replaced by the corresponding original
    transformed under the selected policy (``token`` returns the text unchanged).
    Korean token labels are token-only and reject residual-disclosure policies.
    Unknown/None policies normalize to ``token`` (safest). See
    ``docs/DEIDENTIFICATION_POLICY.md`` for residual-disclosure levels.
    """
    selected = normalize_deidentification_policy(policy)
    if korean_tokens and selected != "token":
        raise ValueError("korean_tokens requires token policy")
    if selected == "token":
        return masked_text

    buckets: dict[str, list[str]] = {}
    for match in matches:
        buckets.setdefault(match.tag, []).append(match.text)
    offsets: dict[str, int] = {}
    transform_state = state if state is not None else TransformState()

    def repl(match: re.Match[str]) -> str:
        tag = match.group("tag")
        values = buckets.get(tag)
        idx = offsets.get(tag, 0)
        if not values or idx >= len(values):
            return match.group(0)
        offsets[tag] = idx + 1
        return transform_value(tag, values[idx], selected, transform_state)

    return TOKEN_PAT.sub(repl, masked_text)

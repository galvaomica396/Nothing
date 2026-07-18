from __future__ import annotations

import re
import unittest
import unicodedata
from pathlib import Path

import document_masker_ocr_gui as masker
import privacy_transformers

from test_frontend_state_helpers import run_node_helper


REPO_ROOT = Path(__file__).resolve().parents[1]


def quoted_values(source: str, pattern: str) -> set[str]:
    match = re.search(pattern, source, re.DOTALL)
    if match is None:
        raise AssertionError(f"enum declaration not found: {pattern}")
    return set(re.findall(r'"([^\"]+)"', match.group("values")))


class OptionEnumContractTests(unittest.TestCase):
    def test_normalize_opts_preserves_provided_values_and_legacy_defaults(self) -> None:
        marker = object()
        normalized = masker.normalize_opts({"phone": False, "_privacy_detector": marker})

        self.assertFalse(normalized["phone"])
        self.assertIs(marker, normalized["_privacy_detector"])
        self.assertTrue(normalized["rrn"])
        self.assertEqual("official", normalized["profile"])
        self.assertEqual("pdf_safe_report", normalized["output_artifacts"])
        self.assertFalse(normalized["return_text_preview"])

    def test_deidentification_and_display_mode_enum_sets_match_all_runtime_boundaries(self) -> None:
        settings = (REPO_ROOT / "src" / "settingsState.ts").read_text(encoding="utf-8")
        transformers = (REPO_ROOT / "privacy_transformers.py").read_text(encoding="utf-8")
        gui = (REPO_ROOT / "document_masker_ocr_gui.py").read_text(encoding="utf-8")
        engine_entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        expected_policies = {"token", "partial", "pseudonym"}
        policy_sets = [
            quoted_values(settings, r'DEIDENTIFICATION_MODES\s*=\s*\[(?P<values>[^\]]+)\]'),
            quoted_values(transformers, r'POLICIES[^=]*=\s*\{(?P<values>[^}]+)\}'),
            quoted_values(rust, r'opts\.deidentification_policy\.as_str\(\),\s*(?P<values>[^)]*)\)'),
        ]
        for values in policy_sets:
            self.assertEqual(expected_policies, values)

        expected_display_modes = {"black", "label_en", "label_ko", "pseudonym"}
        display_mode_sets = [
            quoted_values(settings, r'DISPLAY_MODES\s*=\s*\[(?P<values>[^\]]+)\]'),
            quoted_values(gui, r'display_mode not in \{(?P<values>[^}]+)\}'),
            quoted_values(engine_entry, r'SAFE_DISPLAY_MODES[^=]*=\s*\{(?P<values>[^}]+)\}'),
            quoted_values(rust, r'opts\.display_mode\.as_str\(\),\s*(?P<values>[^)]*)\)'),
        ]
        for values in display_mode_sets:
            self.assertEqual(expected_display_modes, values)

        merged = run_node_helper(
            "src/settingsState.ts",
            "({ valid: m.mergeSettings({ displayMode: 'pseudonym' }).displayMode, legacy: m.mergeSettings({ displayMode: 'unknown' }).displayMode })",
        )
        self.assertEqual("pseudonym", merged["valid"])
        self.assertEqual("black", merged["legacy"])

    def test_final_save_warning_presentation_keeps_warnings_advisory(self) -> None:
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "m.finalSaveWarningPresentation({ hasReportPath: true, report: { product_checks: { quality_gate_passed: false }, document_redaction: { verification: { residual_hits: 2 }, missing_targets_count: 1 }, review_items: [] } })",
        )

        self.assertEqual("fail", result["stateName"])
        self.assertEqual("잔존 개인정보 후보 있음", result["title"])
        self.assertEqual(
            "잔존 개인정보 후보 2건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
            result["detail"],
        )
        self.assertEqual(
            [
                "잔존 개인정보 후보 2건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
                "마스킹되지 않은 대상 1건이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
                "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.",
            ],
            result["warnings"],
        )

    def test_korean_tokens_rejects_non_token_deidentification_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "korean_tokens requires token policy"):
            privacy_transformers.apply_deidentification_policy(
                "[PHONE]",
                (),
                "partial",
                korean_tokens=True,
            )

    def test_pseudonym_name_pool_adds_suffix_after_exhaustion(self) -> None:
        names_by_index: dict[int, str] = {}
        candidate = 0
        while len(names_by_index) < len(privacy_transformers.NAME_POOL):
            value = f"synthetic-person-{candidate}"
            names_by_index.setdefault(
                privacy_transformers._stable_index("NAME", value, len(privacy_transformers.NAME_POOL)),
                value,
            )
            candidate += 1

        state = privacy_transformers.TransformState()
        first_eight = [
            privacy_transformers.pseudonym_value("NAME", names_by_index[index], state)
            for index in range(len(privacy_transformers.NAME_POOL))
        ]
        self.assertEqual(list(privacy_transformers.NAME_POOL), first_eight)

        collision_value = next(
            f"synthetic-person-{index}"
            for index in range(candidate, candidate + 1000)
            if privacy_transformers._stable_index("NAME", f"synthetic-person-{index}", len(privacy_transformers.NAME_POOL)) == 0
        )
        ninth = privacy_transformers.pseudonym_value("NAME", collision_value, state)

        self.assertEqual(f"{privacy_transformers.NAME_POOL[0]}2", ninth)
        self.assertEqual(ninth, privacy_transformers.pseudonym_value("NAME", collision_value, state))

    def test_pseudonyms_never_normalize_to_any_generated_original_candidate(self) -> None:
        def normalized(value: str) -> str:
            return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())

        generated_candidates = {
            "PHONE": (f"010-0000-{index:04d}" for index in range(1000, 10_000)),
            "EMAIL": (f"user{index}@example.invalid" for index in range(1000, 10_000)),
            "ADDRESS": (f"서울특별시 중구 샘플로 {index}" for index in range(1, 301)),
            "COMPANY": (f"주식회사 샘플{index}" for index in range(100)),
            "RRN": (f"[RRN_{index}]" for index in range(1000, 10_000)),
        }
        pool_candidates = {
            **{
                tag: privacy_transformers.NAME_POOL
                for tag in ("NAME", "LEGAL_PARTY", "APPROVAL_LINE", "ATTORNEY")
            },
            "LAW_FIRM": privacy_transformers.FIRM_POOL,
        }

        for tag, candidates in {**pool_candidates, **generated_candidates}.items():
            for original in candidates:
                pseudonym = privacy_transformers.pseudonym_value(tag, original, privacy_transformers.TransformState())
                self.assertNotEqual(normalized(original), normalized(pseudonym), (tag, original))

    def test_pseudonyms_are_unique_per_tag_and_repeat_deterministically(self) -> None:
        values_by_tag = {
            "NAME": [*privacy_transformers.NAME_POOL, "추가 인물"],
            "LAW_FIRM": [*privacy_transformers.FIRM_POOL, "추가 법무법인"],
            "PHONE": [f"010-0000-{index:04d}" for index in range(1000, 1012)],
            "EMAIL": [f"user{index}@example.invalid" for index in range(1000, 1012)],
            "ADDRESS": [f"서울특별시 중구 샘플로 {index}" for index in range(1, 13)],
            "COMPANY": [f"주식회사 샘플{index}" for index in range(12)],
            "RRN": [f"[RRN_{index}]" for index in range(1000, 1012)],
        }

        for tag, originals in values_by_tag.items():
            state = privacy_transformers.TransformState()
            pseudonyms = [privacy_transformers.pseudonym_value(tag, original, state) for original in originals]
            self.assertEqual(len(pseudonyms), len(set(pseudonyms)), tag)
            self.assertEqual(
                pseudonyms,
                [privacy_transformers.pseudonym_value(tag, original, state) for original in originals],
                tag,
            )


if __name__ == "__main__":
    unittest.main()

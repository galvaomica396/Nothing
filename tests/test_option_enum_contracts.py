from __future__ import annotations

import re
import unittest
import unicodedata
from types import SimpleNamespace
import document_masker_ocr_gui as masker
import privacy_transformers

from test_frontend_state_helpers import run_node_helper




class OptionEnumContractTests(unittest.TestCase):
    def test_normalize_opts_preserves_provided_values_and_legacy_defaults(self) -> None:
        marker = object()
        normalized = masker.normalize_opts({"phone": False, "_privacy_detector": marker})

        self.assertFalse(normalized["phone"])
        self.assertIs(marker, normalized["_privacy_detector"])
        self.assertTrue(normalized["rrn"])
        self.assertEqual("pdf_safe_report", normalized["output_artifacts"])
        self.assertFalse(normalized["return_text_preview"])
    def test_python_profile_normalization_preserves_accepted_profiles_and_fails_closed(self) -> None:
        self.assertEqual("mixed", masker.normalize_opts({})["profile"])
        for unsupported in ("official", "default", "unknown"):
            with self.subTest(unsupported=unsupported), self.assertRaisesRegex(ValueError, "MASKING_PROFILE_UNSUPPORTED"):
                masker.normalize_opts({"profile": unsupported})
        for profile in ("internal_review", "official_dispatch", "mixed", "legal"):
            self.assertEqual(profile, masker.normalize_opts({"profile": profile})["profile"])


    def test_profile_normalization_and_native_request_serialization(self) -> None:
        profiles = run_node_helper(
            "src/settingsState.ts",
            "(() => { const persistedOfficial = { getItem: () => JSON.stringify({ profile: 'official' }), setItem() {}, removeItem() {} }; const invalid = (() => { try { m.mergeSettings({ profile: 'unknown' }); return false; } catch { return true; } })(); return { canonical: ['internal_review', 'official_dispatch', 'mixed', 'legal'].map((profile) => m.mergeSettings({ profile }).profile), legacy: m.loadSettings(persistedOfficial).settings.profile, invalid }; })()",
        )
        self.assertEqual(
            profiles["canonical"],
            ["internal_review", "official_dispatch", "mixed", "legal"],
        )
        self.assertEqual(profiles["legacy"], "mixed")
        self.assertTrue(profiles["invalid"])

        requests = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            "(() => { const options = (profile) => ({ rrn: true, phone: true, business_reg: true, name: true, address: true, place: true, legal_party: true, company: true, court: true, case_title: true, case_number: true, law_firm: true, attorney: true, approval_line: true, region_context: true, doc_meta: true, email: true, pdf_redaction: true, custom_keywords: '', extract_engine: 'pypdf', profile, output_artifacts: 'pdf_safe_report', display_mode: 'black', deidentification_policy: 'token', region_scope: 'national', custom_regions: '', return_text_preview: false, auto_mask_threshold: 0.85, review_threshold: 0.5 }); const calls = []; const invoke = (command, payload) => { calls.push({ command, payload }); return Promise.resolve(null); }; for (const profile of ['internal_review', 'official_dispatch', 'mixed']) m.analyzeMaskingRun(invoke, { inputFile: '/tmp/input.pdf', profile, options: options(profile) }); const reject = (profile, optionProfile = profile) => { const before = calls.length; try { m.analyzeMaskingRun(invoke, { inputFile: '/tmp/input.pdf', profile, options: options(optionProfile) }); return { message: '', invoked: calls.length - before }; } catch (error) { return { message: error.message, invoked: calls.length - before }; } }; return { calls, rejected: { unsupported: reject('legal'), mismatched: reject('mixed', 'official_dispatch'), unknown: reject('unknown'), null: reject(null), numeric: reject(7), malformed: reject({ profile: 'mixed' }) } }; })()"
        )
        self.assertEqual([call["command"] for call in requests["calls"]], ["analyze_masking_run"] * 3)
        self.assertEqual(
            [call["payload"]["request"]["options"]["profile"] for call in requests["calls"]],
            ["internal_review", "official_dispatch", "mixed"],
        )
        self.assertEqual(
            {
                key: {"message": "Invalid masking analysis profile.", "invoked": 0}
                for key in ("unsupported", "mismatched", "unknown", "null", "numeric", "malformed")
            },
            requests["rejected"],
        )

    def test_option_enums_are_serialized_at_the_command_boundary(self) -> None:
        values = run_node_helper(
            "src/settingsState.ts",
            "(() => { const reject = (field, value) => { try { m.mergeSettings({ [field]: value }); return false; } catch { return true; } }; return { policies: ['token', 'partial', 'pseudonym'].map((deidentificationMode) => m.mergeSettings({ deidentificationMode }).deidentificationMode), displays: ['black', 'label_en', 'label_ko', 'pseudonym'].map((displayMode) => m.mergeSettings({ displayMode }).displayMode), scopes: ['national', 'seoul', 'custom'].map((regionScope) => m.mergeSettings({ regionScope }).regionScope), rejected: { policy: reject('deidentificationMode', 'unsafe'), display: reject('displayMode', 'emoji'), scope: reject('regionScope', 'mars') } }; })()"
        )
        self.assertEqual(values["policies"], ["token", "partial", "pseudonym"])
        self.assertEqual(values["displays"], ["black", "label_en", "label_ko", "pseudonym"])
        self.assertEqual(values["scopes"], ["national", "seoul", "custom"])
        self.assertEqual({"policy": True, "display": True, "scope": True}, values["rejected"])

        phone = SimpleNamespace(tag="PHONE", text="010-0000-0000")
        token = privacy_transformers.apply_deidentification_policy("[PHONE]", [phone], "token")
        partial = privacy_transformers.apply_deidentification_policy("[PHONE]", [phone], "partial")
        pseudonym = privacy_transformers.apply_deidentification_policy("[PHONE]", [phone], "pseudonym")
        self.assertEqual("[PHONE]", token)
        self.assertEqual("010-****-0000", partial)
        self.assertEqual(
            privacy_transformers.pseudonym_value("PHONE", "010-0000-0000", privacy_transformers.TransformState()),
            pseudonym,
        )
        for rendered in (token, partial, pseudonym):
            self.assertNotIn("010-0000-0000", rendered)

        requests = run_node_helper(
            "src/services/tauri/maskingContracts.ts",
            "(() => { const options = (overrides = {}) => ({ rrn: true, phone: true, business_reg: true, name: true, address: true, place: true, legal_party: true, company: true, court: true, case_title: true, case_number: true, law_firm: true, attorney: true, approval_line: true, region_context: true, doc_meta: true, email: true, pdf_redaction: true, custom_keywords: '', extract_engine: 'pypdf', profile: 'mixed', output_artifacts: 'pdf_safe_report', display_mode: 'black', deidentification_policy: 'token', region_scope: 'national', custom_regions: '', return_text_preview: false, auto_mask_threshold: 0.85, review_threshold: 0.5, ...overrides }); const calls = []; const invoke = (command, payload) => { calls.push({ command, payload }); return Promise.resolve({ command }); }; const valid = [['black','token','national',''], ['label_en','partial','seoul',''], ['label_ko','pseudonym','custom','서울 중구']].map(([display_mode, deidentification_policy, region_scope, custom_regions]) => m.analyzeMaskingRun(invoke, { inputFile: '/tmp/input.pdf', profile: 'mixed', options: options({ display_mode, deidentification_policy, region_scope, custom_regions }) })); const reject = (overrides) => { const before = calls.length; try { m.analyzeMaskingRun(invoke, { inputFile: '/tmp/input.pdf', profile: 'mixed', options: options(overrides) }); return { message: '', invoked: calls.length - before }; } catch (error) { return { message: error.message, invoked: calls.length - before }; } }; return { valid, calls, rejected: { emailString: reject({ email: 'yes' }), displayString: reject({ display_mode: 'emoji' }), displayNull: reject({ display_mode: null }), displayNumber: reject({ display_mode: 7 }), policyString: reject({ deidentification_policy: 'unsafe' }), policyNull: reject({ deidentification_policy: null }), scopeString: reject({ region_scope: 'mars' }), scopeNull: reject({ region_scope: null }), customMissing: reject({ region_scope: 'custom', custom_regions: '' }), customUnexpected: reject({ region_scope: 'seoul', custom_regions: '서울 중구' }) } }; })()",
        )
        self.assertEqual(
            [(item["display_mode"], item["deidentification_policy"], item["region_scope"], item["custom_regions"]) for item in [call["payload"]["request"]["options"] for call in requests["calls"]]],
            [("black", "token", "national", ""), ("label_en", "partial", "seoul", ""), ("label_ko", "pseudonym", "custom", "서울 중구")],
        )
        self.assertEqual(
            {
                key: {"message": "Invalid masking analysis options.", "invoked": 0}
                for key in (
                    "emailString", "displayString", "displayNull", "displayNumber", "policyString", "policyNull",
                    "scopeString", "scopeNull", "customMissing", "customUnexpected",
                )
            },
            requests["rejected"],
        )

    def test_legal_gate_and_presentation_share_advisory_policy_for_quality_states_and_missing_path(self) -> None:
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => { const pass = { product_checks: { quality_gate_passed: true } }; const fail = { product_checks: { quality_gate_passed: false } }; const project = (report, hasReportPath) => ({ gate: m.legalCompatibilityFinalSaveGate({ report, hasReportPath }), presentation: m.finalSaveWarningPresentation({ report, hasReportPath }) }); return { passed: project(pass, true), failed: project(fail, true), missingPath: project(pass, false) }; })()",
        )
        self.assertEqual({"eligible": True, "state": "eligible", "reasonCodes": []}, result["passed"]["gate"])
        self.assertEqual("pass", result["passed"]["presentation"]["stateName"])
        self.assertEqual([], result["passed"]["presentation"]["warnings"])
        self.assertEqual({"eligible": True, "state": "advisory", "reasonCodes": ["legal_quality_gate_failed"]}, result["failed"]["gate"])
        self.assertEqual("review", result["failed"]["presentation"]["stateName"])
        self.assertEqual(
            "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.",
            result["failed"]["presentation"]["detail"],
        )
        self.assertEqual(result["failed"]["presentation"]["warnings"][0], result["failed"]["presentation"]["detail"])
        self.assertEqual(
            {"eligible": False, "state": "blocked", "reasonCodes": ["missing_legal_report_path"]},
            result["missingPath"]["gate"],
        )
        self.assertEqual("최종 저장 차단", result["missingPath"]["presentation"]["title"])
        self.assertEqual(result["missingPath"]["presentation"]["warnings"][0], result["missingPath"]["presentation"]["detail"])

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

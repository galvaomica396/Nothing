from __future__ import annotations

import unittest

from test_frontend_state_helpers import run_node_helper


class SettingsModalTests(unittest.TestCase):
    def test_store_builds_complete_boolean_options_for_public_and_legal_runs(self) -> None:
        result = run_node_helper(
            "src/state/settingsStore.ts",
            "(() => {"
            "const defaultOptions = m.currentMaskingOptions();"
            "m.applySettings({ theme: 'dark', outputDir: '/tmp/masked', profile: 'mixed', engine: 'pymupdf', displayMode: 'label_ko', deidentificationMode: 'partial', regionScope: 'custom', customRegions: '서울 중구', customKeywords: '홍길동', pdfRedaction: false, exportMaskedText: true, openOutputAfterSave: true });"
            "m.setRule('phone', false); m.setRule('approval_line', false); m.setRule('region_context', false); m.setRule('doc_meta', false); const publicOptions = m.currentMaskingOptions();"
            "m.setRule('approval_line', true); m.setRule('region_context', true); m.setRule('doc_meta', true); m.updateSettings({ profile: 'legal' }); const legalOptions = m.currentMaskingOptions();"
            "return { defaultOptions, publicOptions, legalOptions };"
            "})()",
            browser_fixture=True,
        )

        rule_fields = [
            "rrn", "phone", "business_reg", "name", "address", "place", "legal_party", "company",
            "court", "case_title", "case_number", "law_firm", "attorney", "approval_line",
            "region_context", "doc_meta", "email", "pdf_redaction",
        ]
        for options in result.values():
            self.assertTrue(all(isinstance(options[field], bool) for field in rule_fields))
        self.assertTrue(result["defaultOptions"]["phone"])
        self.assertFalse(result["publicOptions"]["phone"])
        self.assertEqual("mixed", result["publicOptions"]["profile"])
        self.assertEqual("서울 중구", result["publicOptions"]["custom_regions"])
        self.assertEqual("pymupdf", result["publicOptions"]["extract_engine"])
        self.assertFalse(result["publicOptions"]["pdf_redaction"])
        self.assertFalse(result["publicOptions"]["approval_line"])
        self.assertFalse(result["publicOptions"]["region_context"])
        self.assertFalse(result["publicOptions"]["doc_meta"])
        self.assertEqual("legal", result["legalOptions"]["profile"])
        self.assertFalse(result["legalOptions"]["approval_line"])
        self.assertFalse(result["legalOptions"]["region_context"])
        self.assertFalse(result["legalOptions"]["doc_meta"])

    def test_settings_store_keeps_rules_and_persisted_settings_in_react_state(self) -> None:
        result = run_node_helper(
            "src/state/settingsStore.ts",
            "(() => {"
            "m.applySettings({ theme: 'dark', outputDir: '', profile: 'mixed', engine: 'auto', displayMode: 'black', deidentificationMode: 'token', regionScope: 'national', customRegions: '', customKeywords: '', pdfRedaction: true, exportMaskedText: false, openOutputAfterSave: true });"
            "m.setRule('phone', false); m.updateSettings({ profile: 'legal' }); const legal = m.settingsSnapshot(); m.beginSettingsDraft(); m.updateSettings({ displayMode: 'pseudonym' }); const cancelled = m.cancelSettingsDraft(); const saved = m.saveCurrentSettings(); return { legal, cancelled, saved, snapshot: m.settingsSnapshot() };"
            "})()",
            browser_fixture=True,
        )
        self.assertFalse(result["legal"]["rules"]["phone"])
        self.assertFalse(result["legal"]["rules"]["approval_line"])
        self.assertTrue(result["cancelled"])
        self.assertEqual("black", result["snapshot"]["settings"]["displayMode"])
        self.assertEqual("dark", result["saved"]["settings"]["theme"])
        self.assertEqual("legal", result["saved"]["settings"]["profile"])


if __name__ == "__main__":
    unittest.main()

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_kr_regions.py"
spec = importlib.util.spec_from_file_location("update_kr_regions", SCRIPT_PATH)
update_kr_regions = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(update_kr_regions)


class UpdateKrRegionsTests(unittest.TestCase):
    def test_official_legal_dong_tsv_is_split_into_region_levels(self):
        official_tsv = "\n".join(
            [
                "법정동코드\t법정동명\t폐지여부",
                "1100000000\t서울특별시\t존재",
                "1111000000\t서울특별시 종로구\t존재",
                "1111010100\t서울특별시 종로구 청운동\t존재",
                "3611000000\t세종특별자치시\t존재",
                "3611010100\t세종특별자치시 반곡동\t존재",
                "4100000000\t경기도\t존재",
                "4111000000\t경기도 수원시\t존재",
                "4111700000\t경기도 수원시 영통구\t존재",
                "4111710300\t경기도 수원시 영통구 이의동\t존재",
                "4182000000\t경기도 가평군\t존재",
                "4182025000\t경기도 가평군 가평읍\t존재",
                "4182025021\t경기도 가평군 가평읍 읍내리\t존재",
                "1111019900\t서울특별시 종로구 폐지동\t폐지",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legal_dong.tsv"
            source.write_text(official_tsv, encoding="utf-8")
            payload = update_kr_regions.build_region_seed(source)

        self.assertFalse(payload["is_seed"])
        self.assertIn("서울특별시", payload["sido"])
        self.assertIn("세종특별자치시", payload["sido"])
        self.assertIn("수원시 영통구", payload["sigungu"])
        self.assertIn("영통구", payload["sigungu"])
        self.assertIn("청운동", payload["eupmyeondong"])
        self.assertIn("반곡동", payload["eupmyeondong"])
        self.assertIn("가평읍", payload["eupmyeondong"])
        self.assertIn("읍내리", payload["ri"])
        self.assertIn("세종특별자치시", payload["single_tier_sido"])
        self.assertNotIn("폐지동", payload["eupmyeondong"])

    def test_single_occurrence_three_char_dong_ri_are_weak_places(self):
        # review L4: a 3-char 동/리 that appears exactly once nationwide must
        # still land in weak_place_names, otherwise place detection is blind to
        # it. Every 동/리 below occurs a single time in this fixture.
        official_tsv = "\n".join(
            [
                "법정동코드\t법정동명\t폐지여부",
                "4100000000\t경기도\t존재",
                "4182000000\t경기도 가평군\t존재",
                "4182025000\t경기도 가평군 가평읍\t존재",
                "4182025021\t경기도 가평군 가평읍 읍내리\t존재",
                "1111000000\t서울특별시 종로구\t존재",
                "1111010100\t서울특별시 종로구 청운동\t존재",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legal_dong.tsv"
            source.write_text(official_tsv, encoding="utf-8")
            payload = update_kr_regions.build_region_seed(source)

        weak = payload["weak_place_names"]
        self.assertIn("청운동", weak)
        self.assertIn("읍내리", weak)
        # names that do not end in 동/리 are excluded from the weak tier.
        self.assertNotIn("가평읍", weak)
        # every weak-place term consumed by the detector is a 동/리 suffix name.
        self.assertTrue(all(w.endswith("동") or w.endswith("리") for w in weak))

    def test_two_char_names_keep_legacy_count_gate(self):
        # 2-char names are not consumed by the detector; keep the historical
        # count/legacy gate so single-occurrence 2-char names stay excluded.
        official_tsv = "\n".join(
            [
                "법정동코드\t법정동명\t폐지여부",
                "1100000000\t서울특별시\t존재",
                "1111000000\t서울특별시 종로구\t존재",
                "1111010100\t서울특별시 종로구 신동\t존재",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legal_dong.tsv"
            source.write_text(official_tsv, encoding="utf-8")
            payload = update_kr_regions.build_region_seed(source)

        # "신동" is a single-occurrence 2-char name and not legacy -> excluded.
        self.assertNotIn("신동", payload["weak_place_names"])


if __name__ == "__main__":
    unittest.main()

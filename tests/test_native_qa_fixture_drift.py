from __future__ import annotations

from scripts.generate_native_qa_ambiguous_fixture import (
    OUTPUT_PATH as AMBIGUOUS_FIXTURE,
    assert_ambiguous_common_only,
    pipeline_manifest as ambiguous_pipeline_manifest,
    write_fixture as write_ambiguous_fixture,
)
from scripts.generate_native_qa_clean_fixture import (
    OUTPUT_PATH as CLEAN_FIXTURE,
    assert_clean_manifest,
)
from scripts.generate_native_qa_fixture import (
    OUTPUT_PATH as MIXED_FIXTURE,
    assert_mixed_manifest,
    assert_official_dispatch_manifest,
    pipeline_manifest as mixed_pipeline_manifest,
    write_fixture as write_mixed_fixture,
)
from scripts.generate_native_qa_geometry_fixture import (
    OUTPUT_PATH as GEOMETRY_FIXTURE,
    assert_geometry_review_manifest,
    pipeline_manifest as geometry_pipeline_manifest,
    write_fixture as write_geometry_fixture,
)
from scripts.generate_native_qa_manual_fixture import (
    OUTPUT_PATH as MANUAL_FIXTURE,
    assert_manual_official_dispatch_manifest,
    pipeline_manifest as manual_pipeline_manifest,
    write_fixture as write_manual_fixture,
)


def test_committed_fixtures_match_native_qa_full_route_guards() -> None:
    assert_mixed_manifest(mixed_pipeline_manifest(MIXED_FIXTURE, "mixed"))
    assert_official_dispatch_manifest(
        mixed_pipeline_manifest(MIXED_FIXTURE, "official_dispatch")
    )
    assert_ambiguous_common_only(ambiguous_pipeline_manifest(AMBIGUOUS_FIXTURE))
    assert_geometry_review_manifest(geometry_pipeline_manifest(GEOMETRY_FIXTURE))
    assert_manual_official_dispatch_manifest(manual_pipeline_manifest(MANUAL_FIXTURE))
    assert_clean_manifest(CLEAN_FIXTURE)


def test_native_qa_fixture_generators_are_byte_deterministic(tmp_path) -> None:
    for label, write_fixture in (
        ("mixed", write_mixed_fixture),
        ("ambiguous", write_ambiguous_fixture),
        ("geometry", write_geometry_fixture),
        ("manual", write_manual_fixture),
    ):
        first = tmp_path / f"{label}-first.pdf"
        second = tmp_path / f"{label}-second.pdf"

        write_fixture(first)
        write_fixture(second)

        assert first.read_bytes() == second.read_bytes()

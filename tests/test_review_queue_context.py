import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def frontend_markup():
    # v4 P2: 검토 레일·저장 게이트가 통합 "문서" 화면(CanvasWorkspace)으로 이식됐다.
    component_paths = [
        REPO_ROOT / "src" / "components" / "AppHeader.tsx",
        REPO_ROOT / "src" / "components" / "StatusRibbon.tsx",
        REPO_ROOT / "src" / "components" / "CanvasWorkspace.tsx",
        REPO_ROOT / "src" / "components" / "SettingsScreen.tsx",
    ]
    component_sources = "\n".join(path.read_text(encoding="utf-8") for path in component_paths)
    return (REPO_ROOT / "index.html").read_text(encoding="utf-8") + "\n" + (REPO_ROOT / "src" / "App.tsx").read_text(encoding="utf-8") + "\n" + component_sources


def frontend_script():
    paths = [
        REPO_ROOT / "src" / "main.tsx",
        *sorted((REPO_ROOT / "src" / "legacy").rglob("*.ts")),
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


class ReviewQueueRemovalTests(unittest.TestCase):
    def test_review_queue_workspace_and_runtime_handlers_are_removed(self):
        script = frontend_script()
        markup = frontend_markup()

        self.assertFalse((REPO_ROOT / "src" / "reviewQueue.ts").exists())
        self.assertFalse((REPO_ROOT / "src" / "components" / "workspaces" / "ReviewQueueScreen.tsx").exists())
        self.assertNotIn('data-screen-panel="review"', markup)
        self.assertNotIn('data-screen-target="review"', markup)
        self.assertNotIn('id="review-screen"', markup)
        self.assertNotIn('id="review-queue"', markup)
        self.assertNotIn('id="review-queue-table-body"', markup)
        self.assertNotIn("ReviewQueueScreen", markup)
        self.assertNotIn("../reviewQueue", script)
        self.assertNotIn("createReviewQueueRows", script)
        self.assertNotIn("renderReviewQueue", script)
        self.assertNotIn("openReviewQueueCandidate", script)
        self.assertNotIn("maskReviewQueueCandidate", script)
        self.assertNotIn("ignoreReviewQueueCandidate", script)
        self.assertNotIn("undoReviewQueueCandidate", script)

    def test_review_copy_points_to_manual_correction_not_safe_report(self):
        # v4.1: 안전 리포트가 내부 검증 장치로 내부화되며 사용자 대면 검토 문구는
        # "안전 리포트"가 아니라 수동 보정으로 안내한다. v4.2.0: 하드 차단 게이트와
        # "검토 확인" 어포던스가 폐기되어 마크업의 "수동 검토" 라벨도 사라졌다(권고
        # 문구는 런타임 save-gate 가 산출). 폐지된 검토 큐 문구가 되살아나지 않았는지도
        # 함께 지킨다.
        markup = frontend_markup()
        script = frontend_script()

        self.assertNotIn("안전 리포트", markup)
        self.assertIn("수동 보정", markup)
        # 권고형 전환: 강제 차단 문구·폐지된 "검토 확인" 어포던스가 마크업에 없어야 한다.
        self.assertNotIn("저장할 수 없습니다", markup)
        self.assertNotIn('id="btn-acknowledge-review"', markup)
        self.assertNotIn("검토 큐 확인", markup)
        self.assertNotIn("검토 큐를 확인", script)
        self.assertNotIn("검토 큐 승인", markup)


if __name__ == "__main__":
    unittest.main()

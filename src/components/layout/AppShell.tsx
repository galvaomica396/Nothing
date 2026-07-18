import type { ReactNode } from "react";
import { AppHeader } from "../AppHeader";
import { StatusRibbon } from "../StatusRibbon";

type AppShellProps = {
  readonly children: ReactNode;
};

// v4 셸 (REDESIGN_V4_DARK §1): 좌측 레일 폐지 → 상단 바 2탭+기어.
// 세로 공간 전부를 문서 화면에 준다.
// v4 P3: 모바일 독(work/stage/review 패널 스위처)은 삭제됐다. 통합 문서 화면에서
// work/stage/review 개념이 하나로 재편되며 좁은 폭에서는 스테이지+검토 레일이
// 세로로 자연 스택되어(§0 규칙: 숨김 잔존 금지, 진짜 삭제) 독이 불필요해졌다.
export function AppShell({ children }: AppShellProps) {
  return (
    <main className="dm-shell" aria-label="문서 작업공간">
      <AppHeader />
      <section className="dm-stage" id="workspace-shell">
        {children}
      </section>
      <StatusRibbon />
    </main>
  );
}

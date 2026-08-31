import { useEffect } from "react";
import type { ReactNode } from "react";
import { AppHeader } from "../AppHeader";
import { Sidebar } from "./Sidebar";
import { StatusRibbon } from "../StatusRibbon";
import { resolveTheme } from "../../settingsState";
import { useShellState } from "../../state/shellStore";

type AppShellProps = {
  readonly children: ReactNode;
};

// 2026-08 KRDS 시안 셸: 좌측 248px 사이드바(화면 전환) + 우측 열
// (상단 바 64px / 문서 화면 / 상태 바 28px).
// 모바일 독(work/stage/review 패널 스위처)은 여전히 없다. 좁은 폭에서는
// 사이드바가 아이콘 레일로 줄고 스테이지+검토 레일이 세로로 스택된다.
export function AppShell({ children }: AppShellProps) {
  const shell = useShellState();

  useEffect(() => {
    const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
    const renderTheme = () => {
      document.documentElement.setAttribute("data-theme", resolveTheme(shell.theme, colorScheme.matches));
      document.documentElement.setAttribute("data-theme-preference", shell.theme);
    };
    renderTheme();
    if (shell.theme !== "system") return;
    colorScheme.addEventListener("change", renderTheme);
    return () => colorScheme.removeEventListener("change", renderTheme);
  }, [shell.theme]);

  return (
    <main className="dm-shell" aria-label="문서 작업공간">
      <Sidebar />
      <AppHeader />
      <section className="dm-stage" id="workspace-shell" data-active-screen={shell.activePanel} data-active-nav={shell.activeScreen}>
        {children}
      </section>
      <StatusRibbon />
    </main>
  );
}

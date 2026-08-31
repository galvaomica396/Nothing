import { SymbolIcon } from "../ui/SymbolIcon";
import { activateScreen, setActiveScreen, useShellState } from "../../state/shellStore";
import { useSessionDocumentsState } from "../../state/sessionDocumentsStore";
import { applicationController } from "../../state/appControllerRuntime";

function openSettingsScreen(): void {
  applicationController()?.rememberAuxReturnScreen();
  setActiveScreen("settings");
}

export function Sidebar() {
  const shell = useShellState();
  const session = useSessionDocumentsState();
  const pendingCount = session.documents.reduce((count, item) => count + item.pendingCount, 0);

  return (
    <nav className="dm-sidebar" aria-label="주 화면 전환">
      <div className="dm-sidebar__brand">
        <button className="dm-header__home" type="button" data-screen-target="desk" data-screen-home="true" aria-label="문서 홈" onClick={() => setActiveScreen("desk")}>
          <img className="dm-sidebar__logo" src="/favicon.png" alt="" aria-hidden="true" />
          <span className="dm-sidebar__brand-name">Nothing</span>
        </button>
      </div>
      <div className="dm-sidebar__nav">
        {shell.activeScreen === "desk" ? (
          <button className="dm-sidebar__item is-active" type="button" data-screen-target="desk" data-screen-nav="true" aria-pressed="true" onClick={() => setActiveScreen("desk")}>
            <SymbolIcon name="dashboard" />
            <span className="dm-sidebar__label">문서 데스크</span>
          </button>
        ) : (
          <button className="dm-sidebar__item" type="button" data-screen-target="desk" data-screen-nav="true" aria-pressed="false" onClick={() => setActiveScreen("desk")}>
            <SymbolIcon name="dashboard" />
            <span className="dm-sidebar__label">문서 데스크</span>
          </button>
        )}
        <button className={shell.activeScreen === "documents" ? "dm-sidebar__item is-active" : "dm-sidebar__item"} type="button" data-screen-target="documents" data-screen-nav="true" aria-pressed={shell.activeScreen === "documents"} onClick={() => setActiveScreen("documents")}>
          <SymbolIcon name="find_replace" />
          <span className="dm-sidebar__label">마스킹 작업</span>
        </button>
        <button className={shell.activeScreen === "review-queue" ? "dm-sidebar__item dm-sidebar__item--queue is-active" : "dm-sidebar__item dm-sidebar__item--queue"} type="button" data-screen-target="review-queue" data-screen-nav="true" aria-label="검토 대기 검토 패널 열기" aria-pressed={shell.activeScreen === "review-queue"} onClick={() => activateScreen("review-queue")}>
          <SymbolIcon name="pending_actions" />
          <span className="dm-sidebar__label">검토 대기</span>
          <strong className="dm-sidebar__count" id="sidebar-review-pending-count">{pendingCount}</strong>
        </button>
        <button className={shell.activeScreen === "storage" ? "dm-sidebar__item is-active" : "dm-sidebar__item"} type="button" data-screen-target="storage" data-screen-nav="true" aria-pressed={shell.activeScreen === "storage"} onClick={() => setActiveScreen("storage")}>
          <SymbolIcon name="save" />
          <span className="dm-sidebar__label">저장함</span>
        </button>
      </div>
      <div className="dm-sidebar__foot">
        <button className={shell.activeScreen === "settings" ? "dm-sidebar__item is-active" : "dm-sidebar__item"} type="button" data-screen-target="settings" data-screen-nav="true" aria-pressed={shell.activeScreen === "settings"} onClick={openSettingsScreen}>
          <SymbolIcon name="settings" />
          <span className="dm-sidebar__label">설정</span>
        </button>
      </div>
    </nav>
  );
}

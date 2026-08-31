import { basenameForDashboardPath } from "../dashboardSurfaceModels";
import { applicationController } from "../state/appControllerRuntime";
import { useShellState } from "../state/shellStore";
import { useSessionDocumentsState } from "../state/sessionDocumentsStore";
import { SymbolIcon } from "./ui/SymbolIcon";

export function DocumentDeskScreen() {
  const { activePanel } = useShellState();
  const session = useSessionDocumentsState();
  const query = session.deskQuery.trim().toLocaleLowerCase();
  const documents = session.documents.filter((item) => {
    if (!query) return true;
    return [basenameForDashboardPath(item.path), item.status, item.profileLabel]
      .some((value) => value.toLocaleLowerCase().includes(query));
  });
  const detectedCount = session.documents.reduce((count, item) => count + item.detectedCount, 0);
  const pendingCount = session.documents.reduce((count, item) => count + item.pendingCount, 0);

  return (
    <section id="document-desk-screen" className={activePanel === "desk" ? "dm-desk is-active" : "dm-desk"} data-screen-panel="desk" data-owner="react" aria-label="문서 데스크">
      <div className="dm-desk__scroll">
        <header className="dm-desk__header">
          <div className="dm-desk__titles">
            <h1 className="dm-desk__title">문서 데스크</h1>
            <p className="dm-desk__subtitle">PDF 공문 속 개인정보를 자동으로 찾아 안전하게 가립니다.</p>
          </div>
          <button id="btn-desk-open-pdf" className="dm-btn dm-btn--primary dm-desk__open" type="button" onClick={() => void applicationController()?.pickDeskDocument()}>
            <SymbolIcon name="folder_open" />
            <span>PDF 열기</span>
          </button>
        </header>

        <div className="dm-desk__stats" role="group" aria-label="이번 세션 현황">
          <article className="dm-desk__stat"><span className="dm-desk__stat-label">이번 세션 처리</span><p className="dm-desk__stat-value"><strong id="desk-stat-documents">{session.documents.length}</strong><span className="dm-desk__stat-unit">건</span></p></article>
          <article className="dm-desk__stat"><span className="dm-desk__stat-label">검출한 개인정보</span><p className="dm-desk__stat-value"><strong id="desk-stat-detected">{detectedCount}</strong><span className="dm-desk__stat-unit">항목</span></p></article>
          <article className="dm-desk__stat"><span className="dm-desk__stat-label">검토 대기</span><p className="dm-desk__stat-value"><strong id="desk-stat-pending">{pendingCount}</strong><span className="dm-desk__stat-unit">건</span></p></article>
        </div>

        <div className="dm-desk__hero">
          <div className="dm-desk__hero-icon" aria-hidden="true"><SymbolIcon name="upload_file" /></div>
          <strong className="dm-desk__hero-title">PDF 공문을 여기로 끌어다 놓으세요</strong>
          <span className="dm-desk__hero-desc">또는 파일을 선택하면 개인정보 자동 탐지가 시작됩니다</span>
          <div className="dm-desk__hero-actions"><button id="btn-desk-pick-file" className="dm-btn dm-btn--primary" type="button" onClick={() => void applicationController()?.pickDeskDocument()}><SymbolIcon name="folder_open" /><span>파일 선택</span></button></div>
        </div>

        <section className="dm-desk__recent" aria-label="이 세션의 문서">
          <div className="dm-desk__recent-head"><h2 className="dm-desk__recent-title">최근 문서</h2><span className="dm-desk__recent-note">현재 세션</span></div>
          <div className="dm-desk__table" role="table" aria-label="문서 목록">
            <div className="dm-desk__table-head" role="row"><span role="columnheader">문서명</span><span role="columnheader">상태</span><span role="columnheader">검출</span><span role="columnheader">유형</span><span role="columnheader" aria-label="문서 작업" /></div>
            <div id="desk-recent-list" className="dm-desk__table-body" role="rowgroup">
              {documents.map((item) => (
                <div className="dm-desk__row" role="row" key={item.path}>
                  <strong title={basenameForDashboardPath(item.path)}>{basenameForDashboardPath(item.path)}</strong><span title={item.status}>{item.status}</span><span>{item.detectedCount}건</span><em className="dm-desk__badge" data-state="current-session" title={item.profileLabel}>{item.profileLabel}</em><span className="dm-desk__row-actions" aria-hidden="true">⋯</span>
                </div>
              ))}
            </div>
            <p id="desk-search-empty" className="dm-desk__empty" hidden={documents.length > 0}>검색과 일치하는 현재 세션 문서가 없습니다.</p>
          </div>
        </section>
      </div>
    </section>
  );
}

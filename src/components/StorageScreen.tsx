import { basenameForDashboardPath } from "../dashboardSurfaceModels";
import { applicationController } from "../state/appControllerRuntime";
import { useShellState } from "../state/shellStore";
import { useSessionDocumentsState } from "../state/sessionDocumentsStore";

export function StorageScreen() {
  const { activePanel } = useShellState();
  const session = useSessionDocumentsState();
  const query = session.storageQuery.trim().toLocaleLowerCase();
  const saves = session.saves.filter((item) => !query || basenameForDashboardPath(item.path).toLocaleLowerCase().includes(query));

  return (
    <section id="storage-screen" className={activePanel === "storage" ? "dm-desk dm-storage is-active" : "dm-desk dm-storage"} data-screen-panel="storage" data-owner="react" aria-label="저장함">
      <div className="dm-desk__scroll">
        <header className="dm-desk__header"><div className="dm-desk__titles"><h1 className="dm-desk__title">저장함</h1><p className="dm-desk__subtitle">이번 세션에서 최종 저장한 안전 문서를 보여줍니다. 앱을 다시 시작하면 목록이 비워집니다.</p></div></header>
        <div className="dm-storage__stats" role="group" aria-label="저장 요약">
          <span className="dm-storage__stat is-active">전체 <strong id="storage-result-count">{session.saves.length}</strong></span><span className="dm-storage__stat">이번 세션 <strong id="storage-session-count">{session.saves.length}</strong></span><span className="dm-storage__sort">최신순 <span aria-hidden="true">⌄</span></span>
        </div>
        <section className="dm-desk__recent" aria-label="최종 저장 결과">
          <div className="dm-desk__recent-head"><h2 className="dm-desk__recent-title">저장된 문서</h2><span className="dm-storage__filter">현재 세션만 표시</span></div>
          <div className="dm-desk__table" role="table" aria-label="최종 저장 목록">
            <div className="dm-desk__table-head" role="row"><span role="columnheader">문서명</span><span role="columnheader">마스킹</span><span role="columnheader">크기</span><span role="columnheader">저장 시각</span><span role="columnheader">상태</span><span role="columnheader" aria-label="문서 작업" /></div>
            <div id="storage-save-list" className="dm-desk__table-body" role="rowgroup">
              {saves.map((item) => (
                <div className="dm-desk__row" role="row" key={item.id}>
                  <strong title={basenameForDashboardPath(item.path)}>{basenameForDashboardPath(item.path)}</strong><span>{item.maskCount === null ? "확인 불가" : `${item.maskCount}건`}</span><span>–</span><span>{item.savedAt}</span><em className="dm-desk__badge" data-state="saved">저장 완료</em><span className="dm-desk__row-actions"><button type="button" aria-label="문서 열기" title="문서 열기" onClick={() => void applicationController()?.openStoredDocument(item.path)}>↗</button><span aria-hidden="true">⋯</span></span>
                </div>
              ))}
            </div>
            <p id="storage-search-empty" className="dm-desk__empty" hidden={saves.length > 0}>검색과 일치하는 현재 세션 저장 문서가 없습니다.</p>
          </div>
        </section>
      </div>
    </section>
  );
}

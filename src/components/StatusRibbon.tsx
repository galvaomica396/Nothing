import { Toast } from "./ui/Toast";

export function StatusRibbon() {
  return (
    <footer className="dm-statusbar" aria-label="실행 상태">
      <Toast id="status" tone="idle">대기 중</Toast>
      <div id="status-detail">경로 - · 페이지 0/0 · 저장 -</div>
    </footer>
  );
}

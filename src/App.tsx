import { CanvasWorkspace } from "./components/CanvasWorkspace";
import { DocumentDeskScreen } from "./components/DocumentDeskScreen";
import { AppShell } from "./components/layout/AppShell";
import { MaskingSettingsScreen } from "./components/MaskingSettingsScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { StorageScreen } from "./components/StorageScreen";
import { AppCompositionRoot } from "./app/AppCompositionRoot";

// 2026-08 KRDS 시안: 문서 데스크(A)가 시작 화면, 마스킹 작업(B)이 검토 화면,
// 저장함은 현재 세션 저장 결과, 설정은 보조 화면으로 연다.
export function App() {
  return (
    <>
      <AppShell>
        <DocumentDeskScreen />
        <CanvasWorkspace />
        <MaskingSettingsScreen />
        <StorageScreen />
        <SettingsScreen />
      </AppShell>
      <AppCompositionRoot />
    </>
  );
}

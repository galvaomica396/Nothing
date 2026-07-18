import { CanvasWorkspace } from "./components/CanvasWorkspace";
import { AppShell } from "./components/layout/AppShell";
import { MaskingSettingsScreen } from "./components/MaskingSettingsScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { LegacyBootstrap } from "./legacy/LegacyBootstrap";

// 문서 관제와 수동 보정은 하나의 문서 화면에 있고, 설정은 보조 화면으로 연다.
export function App() {
  return (
    <>
      <AppShell>
        <CanvasWorkspace />
        <MaskingSettingsScreen />
        <SettingsScreen />
      </AppShell>
      <LegacyBootstrap />
    </>
  );
}

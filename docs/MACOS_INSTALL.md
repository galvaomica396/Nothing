# macOS 설치 안내

Nothing macOS 빌드는 **Apple Silicon(M1 이상) 전용**이며(파일명 `-macos-arm64`),
Apple 개발자 인증서 서명/공증(notarization) 없이 ad-hoc 서명으로 배포됩니다.

## 첫 실행 방법

앱이 인터넷에서 다운로드되면 macOS가 격리(quarantine) 속성을 붙이므로,
처음 열 때 Gatekeeper 확인 절차가 필요합니다.

1. zip 압축을 풀고 `Nothing.app`을 응용 프로그램 폴더로 이동합니다.
2. 그냥 더블클릭하면 "확인되지 않은 개발자" 경고가 뜹니다. 이때:
   - **macOS 15(Sequoia) 이상**: 한 번 실행을 시도한 뒤
     `시스템 설정 → 개인정보 보호 및 보안` 하단의 **"그래도 열기"** 버튼을 누릅니다.
   - **macOS 14 이하**: Finder에서 앱을 **우클릭(Control-클릭) → 열기 → 열기**.
3. 위 절차가 번거로우면 터미널에서 격리 속성을 직접 제거할 수 있습니다:
   ```bash
   xattr -cr /Applications/Nothing.app
   ```

## "손상되었기 때문에 열 수 없습니다"가 뜨는 경우

v3.0.0 릴리즈는 CI가 번들 서명을 누락해 이 오류가 발생했습니다(수정됨 — v3.1.0부터
빌드 단계에서 `codesign --verify --deep --strict` 검증을 통과해야 배포됩니다).
v3.0.0 사용자는 v3.1.0 이상으로 재다운로드하거나, 임시로 아래 명령으로 복구할 수
있습니다:

```bash
xattr -cr /Applications/Nothing.app
codesign --force --deep --sign - /Applications/Nothing.app
```

## 정식 서명/공증 (선택)

Apple Developer Program(연 $99) 계정이 있으면 Developer ID 서명 + 공증으로
경고 없이 배포할 수 있습니다. Tauri 설정(`signingIdentity`)과 CI 시크릿
(`APPLE_CERTIFICATE` 등) 연동이 필요하며, 현재는 미적용 상태입니다.

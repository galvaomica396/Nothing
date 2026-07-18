<div align="center">

# Nothing

**PDF 속 개인정보를, 아무것도 남기지 않고.**

문서를 열고 · 가리고 · 확인하고 · 저장하기까지 — 전부 당신의 PC 안에서.
원문은 어떤 외부 서비스로도 전송되지 않습니다.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey.svg)
![Stack](https://img.shields.io/badge/Tauri-React%20%2B%20Rust%20%2B%20Python-24C8DB.svg)
![Offline](https://img.shields.io/badge/처리-100%25%20로컬-success.svg)

</div>

---

## Nothing이 하는 일

공문서·계약서·신청서 같은 PDF에는 이름, 주민등록번호, 전화번호, 계좌번호처럼
가려야 할 정보가 흩어져 있습니다. **Nothing**은 이를 로컬에서 탐지해 마스킹하고,
저장 전에 사람이 직접 검토·보정할 수 있게 해 줍니다.

```
 PDF 추가  →  자동 마스킹  →  검토 · 수동 보정  →  저장
```

자동 검증 결과는 저장 전 **권고**로만 표시되며, 최종 저장 여부는 언제나 사용자가
결정합니다. Nothing은 판단을 대신하지 않고, 판단을 돕습니다.

## ✨ 주요 기능

| | |
|---|---|
| 🔍 **정밀 탐지** | 이름·주민등록번호·전화·계좌·카드·여권·주소 등 한국 공문서에 특화. 룰 엔진 + 사전·체크섬 보강 레이어 |
| 🖊️ **마스킹 표시 4종** | 검정 박스 · 영문 라벨 · 한글 라벨 · **일관 가명**(같은 사람은 항상 같은 가명) |
| ✋ **수동 보정** | 원문을 다시 드러내지 않는 안전한 박스 추가·복원 검토 |
| 📚 **일괄 처리** | 여러 PDF 대기열 처리와 실패 항목 재실행 |
| 📄 **비식별 TXT** | 완전 치환 · 부분 마스킹 · 일관 가명 중 선택 저장 |
| 🌗 **테마** | 라이트 · 다크 · 시스템 자동 |
| 🔒 **흔적 없음** | 리포트·로그에 원문 값이나 가명 매핑을 남기지 않음 |

> [!IMPORTANT]
> 자동 탐지는 완전성을 보장하지 않습니다. 스캔 품질·텍스트 레이어·글꼴·인코딩에
> 따라 일부가 누락될 수 있으므로, **저장 전 원문과 결과 PDF를 반드시 비교**하세요.
> 최종 확인 책임은 사용자에게 있습니다.

## 🔐 개인정보 처리 원칙

- 모든 문서 처리는 **로컬에서만** 수행합니다. 네트워크 전송이 없습니다.
- 원문 추출 TXT는 사용자 산출 폴더에 저장하지 않습니다.
- 내부 검증 리포트는 OS 임시 디렉터리에서만 쓰고, 원문·가명 매핑을 남기지 않습니다.

자세한 내용은 [`docs/SECURITY_AND_PRIVACY.md`](docs/SECURITY_AND_PRIVACY.md)와
[`docs/DEIDENTIFICATION_POLICY.md`](docs/DEIDENTIFICATION_POLICY.md)에 있습니다.

## 🧩 구성

React · TypeScript UI를 Tauri의 Rust 브리지가 감싸고, 실제 탐지·마스킹은
Python 엔진이 담당합니다. 배포 빌드는 Python 엔진을 함께 패키징하므로 일반
사용자는 Python을 따로 설치할 필요가 없습니다.

**지원 환경** — Windows 10+ · macOS 12+

## 🚀 개발 환경 실행

```bash
npm ci
python3 -m pip install -r requirements.txt
npm run tauri dev      # 프런트엔드만: npm run dev
```

## 🏗️ 빌드

```bash
# 1) OS별 Python 엔진 패키징
./scripts/build_masking_engine.sh        # macOS
.\scripts\build_masking_engine.ps1       # Windows (PowerShell)

# 2) 앱 빌드
npm ci
npm run tauri build
```

플랫폼별 수동 확인 절차는
[`docs/MACOS_INSTALL.md`](docs/MACOS_INSTALL.md) ·
[`docs/WINDOWS_RELEASE_TEST.md`](docs/WINDOWS_RELEASE_TEST.md) 참고.

## ✅ 테스트

```bash
python3 -m unittest discover -s tests      # Python 엔진 + 계약
node scripts/check_runtime_contract.mjs    # UI 셀렉터 계약
cargo test --manifest-path src-tauri/Cargo.toml
npm run build
npm run qa:all                             # 브라우저 스모크 · 저장 흐름 · 옵션
```

## 🗂️ 행정구역 데이터

`data/kr_regions.json`은 행정표준코드관리시스템의 법정동코드 전체자료에서 생성한
데이터입니다. 출처·생성 정보는 파일 메타데이터와
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)에 기록되어 있습니다.

## 📄 라이선스

**Nothing**은 [GNU Affero General Public License v3.0](LICENSE)으로 배포됩니다.
의존성·데이터 출처 고지는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를
참고하세요.

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
가려야 할 정보가 흩어져 있습니다. **Nothing**은 이를 로컬에서 탐지해 **진짜로
제거**하고, 저장 전에 사람이 직접 검토·보정할 수 있게 해 줍니다.

```
 PDF 추가  →  자동 마스킹  →  검토 · 수동 보정  →  저장
```

자동 검증 결과는 저장 전 **권고**로만 표시되며, 최종 저장 여부는 언제나 사용자가
결정합니다. Nothing은 판단을 대신하지 않고, 판단을 돕습니다.

## ⚠️ 왜 "레닥션"인가 — 검은 박스는 가린 게 아닙니다

가장 흔한 오해가, **텍스트 위에 검은 사각형을 덮으면 정보가 지워졌다고 믿는 것**입니다.
아닙니다. PDF에서 도형·주석으로 얹은 검은 박스는 **눈에만 안 보일 뿐, 그 아래 원본
글자는 문서 안에 그대로 살아 있습니다.**

```
검은 박스로 "가린" PDF
   └─ 겉보기:  ██████████  (안 보임)
   └─ 실제:    "홍길동 880101-1234567"  ← 여전히 문서 안에 존재

  · 복사/붙여넣기 →  원문 그대로 나옴
  · Ctrl+A 전체선택 →  가린 글자까지 선택됨
  · OCR / PDF 파서 →  텍스트 추출됨
  · LLM·AI 파이프라인에 투입 →  모델이 가린 내용을 전부 읽음
```

특히 요즘처럼 문서를 **LLM에 넣어 요약·분석**하는 흐름에서는 치명적입니다. 사람 눈엔
가려 보여도, 모델은 검은 박스 밑 텍스트 레이어를 그대로 읽어 개인정보가 통째로
새어 나갑니다.

**진짜 레닥션(redaction)은 "덮는 것"이 아니라 "제거하는 것"입니다.** Nothing은
가릴 영역의 **텍스트·이미지 내용 자체를 문서에서 삭제**한 뒤 그 자리에 표시를 그립니다
(PyMuPDF의 실제 redaction 적용). 그래서 저장된 PDF는 복사해도, OCR을 돌려도,
LLM에 넣어도 **원문을 복원할 수 없습니다.** 이것이 검은 박스와 근본적으로 다른 점이고,
이 도구를 쓰는 이유입니다.

## ✨ 주요 기능

| | |
|---|---|
| 🔍 **정밀 탐지** | 이름·주민등록번호·전화·계좌·카드·여권·주소 등 한국 공문서에 특화. 룰 엔진 + 사전·체크섬 보강 레이어. 결재선/직책 문맥 가중치로 이름 오탐 억제 |
| 🗑️ **진짜 제거** | 검은 박스가 아니라 원본 텍스트·이미지 내용을 삭제하는 실제 redaction. 복사·OCR·LLM으로도 복원 불가 |
| 🖊️ **마스킹 표시 4종** | 검정 박스 · 영문 라벨 · 한글 라벨 · **일관 가명**(같은 사람은 항상 같은 가명) |
| ✋ **수동 보정** | 원문을 다시 드러내지 않는 안전한 박스 추가·복원 검토 |
| 📚 **일괄 처리** | 여러 PDF 대기열 처리와 실패 항목 재실행 |
| 📄 **비식별 TXT** | 완전 치환 · 부분 마스킹 · 일관 가명 중 선택 저장 |
| 🌗 **테마** | 라이트 · 다크 · 시스템 자동 |
| 🔒 **흔적 없음** | 리포트·로그에 원문 값이나 가명 매핑을 남기지 않음 |

> [!IMPORTANT]
> **자동 탐지는 완전하지 않습니다.** Nothing은 외부 서버나 클라우드 AI가 아니라
> **로컬에서 도는 룰·사전 기반 엔진**입니다. 그래서 오프라인·무전송이라는 장점이
> 있는 대신, 스캔 품질·텍스트 레이어·글꼴·인코딩·문서 서식에 따라 일부를 놓치거나
> (드물게) 일반어를 잘못 잡는 한계가 있습니다.
>
> 이 한계를 사람이 메우도록 **수동 보정** 기능을 둡니다 — 자동 마스킹 결과 위에서
> 사용자가 직접 박스를 그려 **가릴 곳을 추가**하거나 **잘못 가린 곳을 복원**할 수
> 있습니다. **저장 전 원문과 결과 PDF를 반드시 비교**하고, 필요한 곳을 손으로
> 보정하세요. 최종 확인 책임은 사용자에게 있습니다.

## 🔐 개인정보 처리 원칙

- 모든 문서 처리는 **로컬에서만** 수행합니다. 네트워크 전송이 없습니다.
- 원문 추출 TXT는 사용자 산출 폴더에 저장하지 않습니다.
- 내부 검증 리포트는 OS 임시 디렉터리에서만 쓰고, 원문·가명 매핑을 남기지 않습니다.

자세한 내용은 [`docs/SECURITY_AND_PRIVACY.md`](docs/SECURITY_AND_PRIVACY.md)와
[`docs/DEIDENTIFICATION_POLICY.md`](docs/DEIDENTIFICATION_POLICY.md)에 있습니다.

## 🧩 구성

React · TypeScript UI를 Tauri의 Rust 브리지가 감싸고, 실제 탐지·레닥션은
Python 엔진이 담당합니다. 배포 빌드는 Python 엔진을 함께 패키징하므로 일반
사용자는 Python을 따로 설치할 필요가 없습니다.

**지원 환경** — Windows 10+ · macOS 12+

## 📥 설치와 첫 실행 (보안 경고 안내)

이 앱은 **상용 코드사이닝 인증서로 서명되어 있지 않습니다.** (개인/오픈소스
프로젝트라 유료 서명 인증서를 붙이지 않았습니다.) 그래서 처음 실행할 때 OS가
"확인되지 않은 개발자"라는 경고를 띄웁니다. **악성이라는 뜻이 아니라 서명이
없다는 뜻**이며, 소스는 이 저장소에 전부 공개되어 있습니다. 아래대로 한 번만
허용하면 이후에는 경고 없이 실행됩니다.

### 🪟 Windows — SmartScreen 경고
`Nothing-<version>-windows-x64-setup.exe`(설치형) 또는 포터블 zip을 실행하면
**"Windows의 PC 보호 (Windows protected your PC)"** 파란 창이 뜰 수 있습니다.

1. 창에서 **`추가 정보 (More info)`** 클릭
2. 아래에 나타나는 **`실행 (Run anyway)`** 클릭

포터블 버전은 압축을 푼 폴더에서 `Nothing-<version>-windows-x64.exe`를 실행하며,
같은 경고가 뜨면 동일하게 처리합니다.

### 🍎 macOS — Gatekeeper 경고
배포 앱은 **ad-hoc 서명**만 되어 있어(Apple Developer 서명 아님), 다운로드한
`.app`을 그냥 더블클릭하면 **"손상되었거나 확인할 수 없어 열 수 없습니다"** 또는
**"확인되지 않은 개발자"** 경고가 뜹니다. 다운로드 격리(quarantine) 속성 때문이며,
다음 중 한 가지로 해제합니다.

- **방법 A (UI):** Finder에서 앱을 **우클릭 → 열기 → 열기**를 한 번 선택하면
  이후엔 그냥 열립니다. (설정 → 개인정보 보호 및 보안 하단의 **"확인 없이 열기"**
  버튼으로도 허용 가능)
- **방법 B (터미널, "손상됨"으로 아예 안 열릴 때):** 격리 속성을 제거합니다.
  ```bash
  xattr -dr com.apple.quarantine "/Applications/Nothing.app"
  ```
  (앱을 다른 위치에 두었다면 그 경로로 바꾸세요.)

> 걱정되시면 서명이 없는 만큼 **소스에서 직접 빌드**해 쓰셔도 됩니다 — 아래
> [빌드](#️-빌드) 절차를 따르면 동일한 앱이 만들어집니다.

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

# Architecture

## Runtime Boundary

- React 19 UI가 단일 PDF 마스킹 흐름과 설정 화면을 소유한다.
- Tauri는 데스크톱 셸, 파일 선택, 번들 리소스 접근, 그리고 프런트엔드와 로컬 런타임 사이 IPC 경계를 담당한다.
- Python 엔진은 PDF 마스킹, 비식별 TXT 변환, 검토 산출물 생성, 수동 박스 적용을 담당한다.

## Product Model

- PDF 흐름: `PDF 추가 -> 기본 마스킹 -> 검토/보정 -> 저장`
- 비식별 TXT는 명시적 추가 산출물이다. PDF와 탐지 결과는 동일하게 유지하면서
  `token`, `partial`, `pseudonym` 중 선택한 변환만 TXT에 적용한다.

## Security Notes

- React는 원문 문서를 외부 서비스로 보내지 않는 로컬 UI다.
- Tauri는 번들된 Python 실행물과 개발용 fallback 사이 실행 경계를 관리한다.
- 원문 추출 TXT는 최종 저장 IPC의 호환 필드로 전달되더라도 게시하지 않는다.
- 좌표 템플릿 IPC 이름과 시그니처는 구 클라이언트 호환을 위해 남지만 모든 호출은
  `FeatureRetired`로 실패하며 실행 경로는 없다.

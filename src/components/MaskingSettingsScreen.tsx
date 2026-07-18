import type { ChangeEvent, MouseEvent } from "react";
import { SymbolIcon } from "./ui/SymbolIcon";

function chooseSettingCard(event: MouseEvent<HTMLButtonElement>) {
  const select = event.currentTarget.parentElement?.previousElementSibling;
  if (!(select instanceof HTMLSelectElement) || select.disabled) return;
  select.value = event.currentTarget.value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function syncSettingCards(event: ChangeEvent<HTMLSelectElement>) {
  const cards = event.currentTarget.nextElementSibling?.children;
  if (!cards) return;
  for (const card of cards) {
    if (!(card instanceof HTMLButtonElement)) continue;
    card.disabled = event.currentTarget.disabled;
    card.setAttribute("aria-pressed", String(card.value === event.currentTarget.value));
  }
}

export function MaskingSettingsScreen() {
  return (
    <section
      id="masking-settings-screen"
      className="dm-settings-screen dm-settings-screen--masking"
      data-screen-panel="masking-settings"
      aria-label="마스킹 설정"
    >
      <header className="dm-settings-topbar">
        <div className="dm-settings-topbar__lead">
          {/* 보조 화면 복귀 어포던스 — 기어("탐지 기준")로 들어온 이전 화면으로 돌아간다. */}
          <button id="btn-masking-settings-back" className="dm-settings-back" type="button">
            <SymbolIcon name="chevron_left" />
            <span>돌아가기</span>
          </button>
          <div className="dm-settings-topbar__heading">
            <span className="dm-settings-kicker">마스킹 설정</span>
            <strong>탐지 기준과 산출물을 설정합니다</strong>
          </div>
        </div>
        <div className="dm-settings-topbar__actions">
          <button id="btn-masking-settings-cancel" className="dm-btn" type="button">변경 취소</button>
          <button id="btn-masking-settings-preview" className="dm-btn" type="button">미리보기</button>
          <button id="btn-masking-settings-apply" className="dm-btn dm-btn--primary" type="button">설정 적용</button>
        </div>
      </header>

      <div className="dm-settings-scroll">
        <div className="dm-settings-grid dm-settings-grid--masking">
          <section className="dm-card dm-settings-card dm-settings-card--rules" aria-label="마스킹 카테고리">
            <div className="dm-card__header">
              <div>
                <span className="dm-card__title">마스킹 카테고리</span>
                <p className="dm-settings-card-hint">체크한 항목만 자동 탐지·마스킹 대상이 됩니다.</p>
              </div>
            </div>
            <div className="dm-card__body">
              <div className="dm-rule-grid" id="rule-grid">
                <label className="dm-rule-card"><input type="checkbox" id="rule-rrn" defaultChecked /><span className="dm-rule-card__label">주민/외국인번호</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-phone" defaultChecked /><span className="dm-rule-card__label">전화번호</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-business_reg" defaultChecked /><span className="dm-rule-card__label">사업자등록번호</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-name" defaultChecked /><span className="dm-rule-card__label">이름</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-address" defaultChecked /><span className="dm-rule-card__label">주소</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-place" defaultChecked /><span className="dm-rule-card__label">지명</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-legal_party" defaultChecked /><span className="dm-rule-card__label">원고/피고</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-company" defaultChecked /><span className="dm-rule-card__label">회사/법인</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-court" defaultChecked /><span className="dm-rule-card__label">법원명</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-case_title" defaultChecked /><span className="dm-rule-card__label">사건명</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-case_number" defaultChecked /><span className="dm-rule-card__label">사건번호</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-law_firm" defaultChecked /><span className="dm-rule-card__label">법무법인</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-attorney" defaultChecked /><span className="dm-rule-card__label">변호사명</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-approval_line" defaultChecked /><span className="dm-rule-card__label">결재선</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-region_context" defaultChecked /><span className="dm-rule-card__label">지역문맥</span></label>
                <label className="dm-rule-card"><input type="checkbox" id="rule-doc_meta" defaultChecked /><span className="dm-rule-card__label">문서메타</span></label>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--document" aria-label="문서와 탐지 방식">
            <div className="dm-card__header">
              <span className="dm-card__title">문서와 탐지 방식</span>
            </div>
            <div className="dm-card__body dm-settings-fields">
              <div className="dm-field">
                <label htmlFor="profile">문서 유형</label>
                <select id="profile" className="dm-select">
                  <option value="official">공공문서</option>
                  <option value="legal">법률문서</option>
                </select>
              </div>
              <div className="dm-field">
                <label htmlFor="engine">PDF 추출 엔진</label>
                <select id="engine" className="dm-select">
                  <option value="auto">자동 선택</option>
                  <option value="marker">Marker PDF</option>
                  <option value="paddle">Paddle OCR</option>
                  <option value="pymupdf">PyMuPDF</option>
                  <option value="pypdf">PyPDF</option>
                </select>
              </div>
              <div className="dm-field dm-choice-field">
                <label htmlFor="display-mode">PDF 가림 모양</label>
                <p className="dm-field-hint">원문은 항상 완전히 가립니다. 그 위에 검정 면, 유형 라벨 또는 생성한 가명을 표시합니다.</p>
                <div className="dm-choice-control">
                  <select id="display-mode" className="dm-select dm-visually-hidden" defaultValue="black" tabIndex={-1} aria-hidden="true" onChange={syncSettingCards}>
                    <option value="black">검정 박스</option>
                    <option value="label_en">영문 라벨</option>
                    <option value="label_ko">한글 라벨</option>
                    <option value="pseudonym">가명 표시</option>
                  </select>
                  <div className="dm-choice-grid dm-choice-grid--four" role="group" aria-label="PDF 가림 모양 선택">
                    <button id="btn-display-mode-black" className="dm-choice-card" type="button" value="black" aria-controls="display-mode" aria-pressed="true" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">검정 박스</span>
                      <span className="dm-choice-card__description">식별 영역을 검정 면으로 완전히 가립니다.</span>
                      <span className="dm-choice-preview">전화: <span className="dm-choice-preview__mask">010-1234-5678</span></span>
                    </button>
                    <button id="btn-display-mode-label-en" className="dm-choice-card" type="button" value="label_en" aria-controls="display-mode" aria-pressed="false" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">영문 라벨</span>
                      <span className="dm-choice-card__description">영문 유형 토큰을 PDF 위에 표시합니다.</span>
                      <span className="dm-choice-preview">전화: <code>[PHONE]</code></span>
                    </button>
                    <button id="btn-display-mode-label-ko" className="dm-choice-card" type="button" value="label_ko" aria-controls="display-mode" aria-pressed="false" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">한글 라벨</span>
                      <span className="dm-choice-card__description">한글 유형 라벨을 PDF 위에 표시합니다.</span>
                      <span className="dm-choice-preview">전화: <code>[전화번호]</code></span>
                    </button>
                    <button id="btn-display-mode-pseudonym" className="dm-choice-card" type="button" value="pseudonym" aria-controls="display-mode" aria-pressed="false" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">가명 표시</span>
                      <span className="dm-choice-card__description">원문을 검정 면으로 가린 뒤 같은 값에 같은 가명을 표시합니다.</span>
                      <span className="dm-choice-preview">홍길동 → <code>박지훈</code></span>
                    </button>
                  </div>
                </div>
              </div>
              <div className="dm-field">
                <span className="dm-field__label">TXT 추가 산출</span>
                <label className="dm-checkbox"><input type="checkbox" id="settings-export-masked-text" /> 비식별 TXT 함께 저장</label>
                <p className="dm-field-hint">켜면 아래 변환 방식을 적용한 TXT를 최종 PDF와 함께 저장합니다. 원문 TXT는 저장하지 않습니다.</p>
              </div>
              <div className="dm-field dm-choice-field">
                <label htmlFor="deidentification-policy">TXT 비식별 변환</label>
                <p className="dm-field-hint">TXT 저장 시에만 적용하며 PDF 가림 모양과 독립적으로 선택합니다.</p>
                <div className="dm-choice-control">
                  <select id="deidentification-policy" className="dm-select dm-visually-hidden" defaultValue="token" tabIndex={-1} aria-hidden="true" onChange={syncSettingCards}>
                    <option value="token">완전 치환 · 유형 토큰</option>
                    <option value="partial">부분 마스킹 · 일부 형식 유지</option>
                    <option value="pseudonym">일관 가명 · 같은 값은 같은 가명</option>
                  </select>
                  <div className="dm-choice-grid" role="group" aria-label="비식별 TXT 변환 방식 선택">
                    <button id="btn-policy-token" className="dm-choice-card" type="button" value="token" aria-controls="deidentification-policy" aria-pressed="true" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">완전 치환</span>
                      <span className="dm-choice-card__description">모든 값을 대표 유형 토큰으로 바꿉니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>[NAME]</code></span><span>010-1234-5678 → <code>[PHONE]</code></span></span>
                    </button>
                    <button id="btn-policy-partial" className="dm-choice-card" type="button" value="partial" aria-controls="deidentification-policy" aria-pressed="false" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">부분 마스킹</span>
                      <span className="dm-choice-card__description">형식을 유지하며 식별 가능한 일부를 가립니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>홍OO</code></span><span>010-1234-5678 → <code>010-****-5678</code></span><span>hong@test.com → <code>h***@test.com</code></span></span>
                    </button>
                    <button id="btn-policy-pseudonym" className="dm-choice-card" type="button" value="pseudonym" aria-controls="deidentification-policy" aria-pressed="false" onClick={chooseSettingCard}>
                      <span className="dm-choice-card__title">일관 가명</span>
                      <span className="dm-choice-card__description">같은 원본 값은 같은 가명으로 바꿉니다.</span>
                      <span className="dm-choice-preview"><span>홍길동 → <code>박지훈</code></span><span>010-1234-5678 → <code>010-0000-1199</code></span></span>
                    </button>
                  </div>
                </div>
              </div>
              <div className="dm-field">
                <label htmlFor="region-scope">지역 범위</label>
                <select id="region-scope" className="dm-select" defaultValue="national">
                  <option value="seoul">서울/수도권</option>
                  <option value="national">전국</option>
                  <option value="custom">사용자 지정 지역</option>
                </select>
              </div>
              <div className="dm-field config-cell">
                <label htmlFor="custom-regions">사용자 지정 지역명</label>
                <input id="custom-regions" className="dm-input" type="text" placeholder="예: ○○특별시 ○○구, ○○동" disabled />
                <p className="dm-field-hint">지역 범위에서 사용자 지정 지역을 선택하면 직접 입력할 수 있습니다.</p>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--safety" aria-label="산출물">
            <div className="dm-card__header">
              <span className="dm-card__title">산출물</span>
            </div>
            <div className="dm-card__body dm-settings-fields">
              <div className="dm-field">
                <span className="dm-field__label">저장 안전장치</span>
                <label className="dm-checkbox"><input type="checkbox" id="opt-pdf-redaction" defaultChecked /> 자동 PDF 레닥션</label>
                <p className="dm-field-hint">PDF 원문 영역을 실제로 제거하고 선택한 가림 모양을 새로 그립니다.</p>
              </div>
            </div>
          </section>

          <section className="dm-card dm-settings-card dm-settings-card--scope" aria-label="현재 작업 적용 상태">
            <div className="dm-card__header">
              <span className="dm-card__title">현재 작업 적용 상태</span>
            </div>
            <div className="dm-card__body">
              <div className="dm-scope-grid">
                <div className="dm-scope-item">
                  <strong>앱 기본값</strong>
                  <span>새 문서를 열 때 기본으로 사용할 마스킹 설정입니다.</span>
                </div>
                <div className="dm-scope-item">
                  <strong>현재 문서 작업값</strong>
                  <span id="settings-apply-scope-status">저장하면 현재 작업에도 적용됩니다.</span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}

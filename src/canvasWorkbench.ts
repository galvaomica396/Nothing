export type CanvasBoxMode = "mask" | "restore";

export type CanvasBox = {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  mode: CanvasBoxMode;
};

export type CanvasBoxRow = {
  globalIndex: number;
  localNumber: number;
  pageLabel: string;
  modeLabel: string;
  label: string;
  selected: boolean;
};

export type CanvasBoxActionArgs = {
  boxes: CanvasBox[];
  currentPage: number;
  selectedBoxIndex: number;
  hasResultDoc: boolean;
};

export const CANVAS_MIN_SCALE = 0.5;
export const CANVAS_MAX_SCALE = 2.5;

export function createCanvasBoxRows(
  boxes: CanvasBox[],
  currentPage: number,
  selectedBoxIndex = -1,
): CanvasBoxRow[] {
  return boxes
    .map((box, globalIndex) => ({ box, globalIndex }))
    .filter(({ box }) => box.page === currentPage)
    .map(({ box, globalIndex }, index) => {
      const modeLabel = box.mode === "mask" ? "마스킹" : "복원";
      const pageLabel = `${currentPage + 1}쪽`;
      const localNumber = index + 1;
      return {
        globalIndex,
        localNumber,
        pageLabel,
        modeLabel,
        // UX_SIMPLICITY_V3_4 §2: 화면에 보이는 라벨은 유형만 — "n번 박스" 식별자·
        // px 좌표/크기·페이지 수치는 노출 금지(박스는 캔버스 위 시각 표현이 곧
        // 상태이며, 행 선택 시 해당 박스가 하이라이트된다). localNumber/pageLabel/
        // size 는 내부 계산·정렬용으로만 유지한다.
        label: modeLabel,
        selected: globalIndex === selectedBoxIndex,
      };
    });
}

export function canvasBoxActionState(args: CanvasBoxActionArgs) {
  const currentRows = createCanvasBoxRows(args.boxes, args.currentPage, args.selectedBoxIndex);
  const selectedBox = args.boxes[args.selectedBoxIndex];
  const selectedOnCurrentPage = Boolean(selectedBox && selectedBox.page === args.currentPage);
  return {
    canDeleteSelected: args.hasResultDoc && selectedOnCurrentPage,
    canApply: args.hasResultDoc && args.boxes.length > 0,
    emptyCurrentPage: currentRows.length === 0,
    currentPageCount: currentRows.length,
  };
}

export function deleteCanvasBoxAtIndex(boxes: CanvasBox[], selectedBoxIndex: number) {
  if (selectedBoxIndex < 0 || selectedBoxIndex >= boxes.length) {
    return { boxes: [...boxes], selectedBoxIndex: -1 };
  }
  const nextBoxes = boxes.filter((_, index) => index !== selectedBoxIndex);
  const nextSelectedBoxIndex = nextBoxes.length === 0 ? -1 : Math.min(selectedBoxIndex, nextBoxes.length - 1);
  return { boxes: nextBoxes, selectedBoxIndex: nextSelectedBoxIndex };
}

export function canvasZoomActionState(scale: number) {
  return {
    minScale: CANVAS_MIN_SCALE,
    maxScale: CANVAS_MAX_SCALE,
    canZoomOut: scale > CANVAS_MIN_SCALE,
    canZoomIn: scale < CANVAS_MAX_SCALE,
  };
}

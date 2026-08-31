import type { PDFDocumentProxy } from "pdfjs-dist";

export type PdfPageThumbnail = {
  readonly pageIndex: number;
  readonly src: string;
};

type PdfThumbnailRendererDeps = {
  readonly getDocument: () => PDFDocumentProxy | null;
  readonly publish: (thumbnails: readonly PdfPageThumbnail[]) => void;
};

const THUMBNAIL_SCALE = 0.14;

export function createPdfThumbnailRenderer(deps: PdfThumbnailRendererDeps): {
  readonly load: (pageIndexes: readonly number[]) => Promise<void>;
} {
  let cachedDocument: PDFDocumentProxy | null = null;
  const cache = new Map<number, string>();

  async function renderPage(pdfDocument: PDFDocumentProxy, pageIndex: number): Promise<PdfPageThumbnail | null> {
    if (pageIndex < 0 || pageIndex >= pdfDocument.numPages) return null;
    const page = await pdfDocument.getPage(pageIndex + 1);
    const viewport = page.getViewport({ scale: THUMBNAIL_SCALE });
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (context === null) return null;
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvas, canvasContext: context, viewport }).promise;
    return { pageIndex, src: canvas.toDataURL("image/png") };
  }

  async function load(pageIndexes: readonly number[]): Promise<void> {
    const currentDocument = deps.getDocument();
    if (currentDocument === null) return;
    if (cachedDocument !== currentDocument) {
      cachedDocument = currentDocument;
      cache.clear();
    }
    const uniquePages = [...new Set(pageIndexes)].filter((pageIndex) => !cache.has(pageIndex));
    if (uniquePages.length === 0) return;
    const rendered = await Promise.all(uniquePages.map((pageIndex) => renderPage(currentDocument, pageIndex)));
    if (cachedDocument !== currentDocument) return;
    for (const thumbnail of rendered) {
      if (thumbnail !== null) cache.set(thumbnail.pageIndex, thumbnail.src);
    }
    deps.publish([...cache].map(([pageIndex, src]) => ({ pageIndex, src })));
  }

  return { load };
}

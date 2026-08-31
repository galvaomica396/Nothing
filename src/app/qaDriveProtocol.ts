import timeoutConfig from "../../contracts/qa-drive-timeouts.json" with { type: "json" };

export const QA_DRIVE_OPEN_TIMEOUT_MS = timeoutConfig.open_ms; // QA_DRIVE_OPEN_TIMEOUT_MS = 180_000 in the canonical config
export const QA_DRIVE_CONTROL_TIMEOUT_MS = timeoutConfig.control_ms;
export const QA_DRIVE_NAVIGATION_TIMEOUT_MS = timeoutConfig.navigation_ms;
export const QA_DRIVE_LONG_TIMEOUT_MS = timeoutConfig.long_ms;
export const QA_DRIVE_RESPONSE_TIMEOUT_MS = timeoutConfig.response_ms;
export const QA_DRIVE_RENDER_CANCEL_TIMEOUT_MS = timeoutConfig.render_cancel_ms;

export type QaDriveCommandKind =
  | "open"
  | "set-profile"
  | "apply-keyword"
  | "set-tool"
  | "start-masking"
  | "run-masking"
  | "wait-idle"
  | "render-probe"
  | "go-page"
  | "scroll-to"
  | "inspect-target"
  | "set-overlay"
  | "resolve-review"
  | "resolve-geometry"
  | "draw-box"
  | "drag-canvas"
  | "apply-manual"
  | "open-save-dialog"
  | "confirm-save"
  | "wait-save"
  | "close-success-dialog"
  | "save-final"
  | "dump-state";

export const QA_DRIVE_COMMAND_TIMEOUTS_MS: Readonly<Record<QaDriveCommandKind, number>> = {
  open: QA_DRIVE_OPEN_TIMEOUT_MS,
  "set-profile": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "apply-keyword": QA_DRIVE_LONG_TIMEOUT_MS,
  "set-tool": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "start-masking": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "run-masking": QA_DRIVE_LONG_TIMEOUT_MS,
  "wait-idle": QA_DRIVE_LONG_TIMEOUT_MS,
  "render-probe": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "go-page": QA_DRIVE_NAVIGATION_TIMEOUT_MS,
  "scroll-to": QA_DRIVE_NAVIGATION_TIMEOUT_MS,
  "inspect-target": QA_DRIVE_NAVIGATION_TIMEOUT_MS,
  "set-overlay": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "resolve-review": QA_DRIVE_LONG_TIMEOUT_MS,
  "resolve-geometry": QA_DRIVE_NAVIGATION_TIMEOUT_MS,
  "draw-box": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "drag-canvas": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "apply-manual": QA_DRIVE_LONG_TIMEOUT_MS,
  "open-save-dialog": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "confirm-save": QA_DRIVE_LONG_TIMEOUT_MS,
  "wait-save": QA_DRIVE_LONG_TIMEOUT_MS,
  "close-success-dialog": QA_DRIVE_CONTROL_TIMEOUT_MS,
  "save-final": QA_DRIVE_LONG_TIMEOUT_MS,
  "dump-state": QA_DRIVE_CONTROL_TIMEOUT_MS,
};

export type QaDriveTraceEvent = {
  readonly stage: string;
  readonly event: "start" | "complete" | "failed";
  readonly elapsedMs?: number;
  readonly errorCode?: string;
  readonly detail?: string;
};

type QaDriveTraceSink = (event: QaDriveTraceEvent) => void;
let activeTraceSink: QaDriveTraceSink | null = null;

function settleQaDriveOperation<T>(
  operation: () => PromiseLike<T> | T,
  resolve: (value: T) => void,
  reject: (error: unknown) => void,
): void {
  try {
    const value = operation();
    if (
      value !== null
      && (typeof value === "object" || typeof value === "function")
      && typeof (value as PromiseLike<T>).then === "function"
    ) {
      (value as PromiseLike<T>).then(resolve, reject);
      return;
    }
    resolve(value as T);
  } catch (error) {
    reject(error);
  }
}

export function activateQaDriveTrace(sink: QaDriveTraceSink): () => void {
  const previous = activeTraceSink;
  activeTraceSink = sink;
  return () => {
    if (activeTraceSink === sink) activeTraceSink = previous;
  };
}

export function traceQaDriveStage(
  stage: string,
  event: QaDriveTraceEvent["event"],
  details: Omit<QaDriveTraceEvent, "stage" | "event"> = {},
): void {
  if (!activeTraceSink) return;
  try {
    activeTraceSink({ stage, event, ...details });
  } catch {
    // Telemetry must never change the command result.
  }
}

function errorCode(error: unknown): string | undefined {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return message.match(/[A-Z][A-Z0-9_]{2,}/)?.[0];
}

export async function measureQaDriveStage<T>(
  stage: string,
  operation: () => PromiseLike<T> | T,
  detail?: string,
): Promise<T> {
  const startedAt = Date.now();
  traceQaDriveStage(stage, "start", detail ? { detail } : {});
  try {
    const value = await operation();
    traceQaDriveStage(stage, "complete", {
      elapsedMs: Math.max(0, Date.now() - startedAt),
      ...(detail ? { detail } : {}),
    });
    return value;
  } catch (error) {
    traceQaDriveStage(stage, "failed", {
      elapsedMs: Math.max(0, Date.now() - startedAt),
      errorCode: errorCode(error),
      ...(detail ? { detail } : {}),
    });
    throw error;
  }
}

export function measureQaDriveStageSync<T>(
  stage: string,
  operation: () => T,
  detail?: string,
): T {
  const startedAt = Date.now();
  traceQaDriveStage(stage, "start", detail ? { detail } : {});
  try {
    const value = operation();
    traceQaDriveStage(stage, "complete", {
      elapsedMs: Math.max(0, Date.now() - startedAt),
      ...(detail ? { detail } : {}),
    });
    return value;
  } catch (error) {
    traceQaDriveStage(stage, "failed", {
      elapsedMs: Math.max(0, Date.now() - startedAt),
      errorCode: errorCode(error),
      ...(detail ? { detail } : {}),
    });
    throw error;
  }
}

export function qaDriveCommandTimeoutMs(kind: string): number {
  return QA_DRIVE_COMMAND_TIMEOUTS_MS[kind as QaDriveCommandKind] ?? QA_DRIVE_CONTROL_TIMEOUT_MS;
}

export function qaDriveTimeoutError(stage: string): Error {
  return new Error(`QA_DRIVE_COMMAND_TIMEOUT:stage=${stage}`);
}

export function qaDriveRenderUnavailableError(stage: string): Error {
  return new Error(`QA_DRIVE_RENDER_UNAVAILABLE:stage=${stage}`);
}

export function qaDriveCancellationError(stage: string): Error {
  return new Error(`QA_DRIVE_COMMAND_CANCELLED:stage=${stage}`);
}

export function isQaDriveTimeoutError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return message.startsWith("QA_DRIVE_COMMAND_TIMEOUT:")
    || message.startsWith("QA_DRIVE_RENDER_UNAVAILABLE:")
    || message.startsWith("QA_DRIVE_RENDER_CANCEL_TIMEOUT:")
    || message.startsWith("QA_DRIVE_COMMAND_CANCELLED:");
}

export function isQaDriveCancellationError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return message.startsWith("QA_DRIVE_COMMAND_CANCELLED:");
}

export function withQaDriveCancellation<T>(
  operation: () => PromiseLike<T> | T,
  signal: AbortSignal | undefined,
  stage: string,
  onAbort?: () => void | PromiseLike<void>,
): Promise<T> {
  if (!signal) {
    try {
      const value = operation();
      return value instanceof Promise ? value : Promise.resolve(value);
    } catch (error) {
      return Promise.reject(error);
    }
  }
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    let aborting = false;
    const finish = (callback: () => void): void => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      callback();
    };
    const abort = (): void => {
      if (aborting) return;
      aborting = true;
      let cleanup: void | PromiseLike<void> = undefined;
      try {
        cleanup = onAbort?.();
      } catch {
        // Cancellation is best effort; the command deadline remains authoritative.
      }
      Promise.resolve(cleanup).then(
        () => finish(() => reject(qaDriveCancellationError(stage))),
        () => finish(() => reject(qaDriveCancellationError(stage))),
      );
    };
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) {
      abort();
      return;
    }
    settleQaDriveOperation(
      () => operation(),
      (value) => {
        if (aborting) return;
        finish(() => resolve(value));
      },
      (error) => {
        if (aborting) return;
        finish(() => reject(error));
      },
    );
  });
}

export function withQaDriveTimeout<T>(
  operation: (signal: AbortSignal) => PromiseLike<T> | T,
  timeoutMs: number,
  stage: string,
  options: {
    readonly signal?: AbortSignal;
    readonly onTimeout?: () => void | PromiseLike<void>;
    readonly timeoutError?: (stage: string) => Error;
    readonly timeoutGraceMs?: number;
  } = {},
): Promise<T> {
  const duration = Math.max(0, Math.floor(timeoutMs));
  return new Promise<T>((resolve, reject) => {
    const controller = new AbortController();
    const parentSignal = options.signal;
    let settled = false;
    let timeoutRequested = false;
    let resolveOperationSettled: () => void = () => undefined;
    const operationSettlement = new Promise<void>((resolveOperation) => {
      resolveOperationSettled = resolveOperation;
    });
    const finish = (callback: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      parentSignal?.removeEventListener("abort", abortFromParent);
      callback();
    };
    const abortFromParent = (): void => {
      if (timeoutRequested) return;
      controller.abort();
      finish(() => reject(qaDriveCancellationError(stage)));
    };
    const timer = setTimeout(() => {
      timeoutRequested = true;
      controller.abort();
      let cleanup: void | PromiseLike<void> = undefined;
      try {
        cleanup = options.onTimeout?.();
      } catch {
        cleanup = undefined;
      }
      const graceMs = Math.max(0, Math.floor(options.timeoutGraceMs ?? 0));
      if (graceMs === 0) {
        finish(() => reject(options.timeoutError?.(stage) ?? qaDriveTimeoutError(stage)));
        return;
      }
      const grace = new Promise<void>((resolveGrace) => {
        setTimeout(resolveGrace, graceMs);
      });
      Promise.race([
        Promise.all([
          Promise.resolve(cleanup).then(() => undefined, () => undefined),
          operationSettlement,
        ]),
        grace,
      ]).then(() => {
        finish(() => reject(options.timeoutError?.(stage) ?? qaDriveTimeoutError(stage)));
      });
    }, duration);
    if (parentSignal?.aborted) {
      abortFromParent();
      return;
    }
    parentSignal?.addEventListener("abort", abortFromParent, { once: true });
    settleQaDriveOperation(
      () => operation(controller.signal),
      (value) => {
        resolveOperationSettled();
        if (timeoutRequested) return;
        finish(() => resolve(value));
      },
      (error) => {
        resolveOperationSettled();
        if (timeoutRequested) return;
        finish(() => reject(error));
      },
    );
  });
}

import assert from "node:assert/strict";

import {
  QA_DRIVE_OPEN_TIMEOUT_MS,
  qaDriveRenderUnavailableError,
  qaDriveCommandTimeoutMs,
  withQaDriveCancellation,
  withQaDriveTimeout,
} from "../src/app/qaDriveProtocol.ts";

const OPEN_TEST_TIMEOUT_MS = 25;
const COMMAND = "open";
const RENDER_STAGE = "canvas-render";
const startedAt = Date.now();

assert.equal(qaDriveCommandTimeoutMs(COMMAND), QA_DRIVE_OPEN_TIMEOUT_MS);
assert.ok(QA_DRIVE_OPEN_TIMEOUT_MS > 30_000, "open must not use the old 30-second client deadline");
let cancelled = false;
await assert.rejects(
  withQaDriveTimeout(
    (signal) => withQaDriveCancellation(
      () => new Promise(() => {}),
      signal,
      RENDER_STAGE,
      () => {
        cancelled = true;
      },
    ),
    OPEN_TEST_TIMEOUT_MS,
    RENDER_STAGE,
    { timeoutError: () => qaDriveRenderUnavailableError(`${RENDER_STAGE}:command=${COMMAND}`) },
  ),
  (error) => error instanceof Error
    && error.message === "QA_DRIVE_RENDER_UNAVAILABLE:stage=canvas-render:command=open",
);
assert.equal(cancelled, true, "render cancellation must be signalled before the timeout response");
assert.ok(Date.now() - startedAt < 1_000, "a non-settling open stage must fail promptly in the drive guard");

console.log("[qa:drive] open render stage has a finite labelled timeout and cancellation");

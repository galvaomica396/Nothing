import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const host = process.env.TAURI_DEV_HOST;

// Build-time version injection: read package.json (single source of truth) and
// expose it as a global constant so the app can display its own version without
// any Tauri API dependency — works in dev, browser QA, and the packaged app.
const pkg = JSON.parse(readFileSync(fileURLToPath(new URL("./package.json", import.meta.url)), "utf8")) as { version: string };

export default defineConfig(async () => ({
  plugins: [react()],

  define: { __APP_VERSION__: JSON.stringify(pkg.version) },

  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    // Split the pdfjs-dist main library out of the app entry chunk. Before this,
    // index-*.js bundled pdfjs statically and weighed ~805 kB, tripping Vite's
    // 500 kB chunk-size warning. Isolating the (large, rarely churning) PDF
    // engine into its own vendor chunk brings both the entry (~395 kB) and the
    // pdfjs vendor chunk (~410 kB) under the limit, so the warning clears while
    // the bundle composition stays legible. Vite 8 uses rolldown, whose grouping
    // option is build.rolldownOptions.output.codeSplitting.groups.
    //
    // Note: pdfjs' web worker (pdf.worker.min-*.mjs, ~1.2 MB) is imported via
    // `?url` in compositionRoot.ts, so it is emitted as a standalone asset, not a
    // JS chunk — asset size does not count toward chunkSizeWarningLimit, and the
    // worker is the minified pdfjs worker that cannot be split further anyway.
    // This is a Tauri desktop app that loads every asset from the local bundle
    // on disk (no HTTP waterfall / CDN caching), so further code-splitting would
    // add no runtime benefit; the split above is purely for bundle legibility.
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [{ name: "pdfjs", test: /[\\/]node_modules[\\/]pdfjs-dist[\\/]/ }],
        },
      },
    },
  },
}));

import { spawnSync } from "node:child_process";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "..");
const args = process.argv.slice(2);

if (args[0] === "build") {
  const preparation = spawnSync(process.execPath, [path.join(repoRoot, "scripts", "prepare_package_fingerprint.mjs"), "--repo", repoRoot], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  if (preparation.error !== undefined) throw preparation.error;
  if (preparation.status !== 0) process.exit(preparation.status ?? 1);
}

const tauriExecutable = path.join(repoRoot, "node_modules", ".bin", process.platform === "win32" ? "tauri.cmd" : "tauri");
const environment = {
  ...process.env,
  ...(process.env.CI === "1" ? { CI: "true" } : {}),
};
const result = spawnSync(tauriExecutable, args, { cwd: repoRoot, stdio: "inherit", env: environment });
if (result.error !== undefined) throw result.error;
process.exitCode = result.status ?? 1;

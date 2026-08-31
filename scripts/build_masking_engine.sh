#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
spec_path="${SPEC_PATH:-$repo_root/packaging/pyinstaller/masking_engine.spec}"
runtime_bin_dir="${RUNTIME_BIN_DIR:-$repo_root/masking_runtime/bin}"

python3 -m PyInstaller --noconfirm "$spec_path"

dist_bin="$repo_root/dist/masking_engine"
if [[ ! -f "$dist_bin" ]]; then
  echo "PyInstaller did not produce $dist_bin" >&2
  exit 1
fi

"$dist_bin" --detector-smoke >/dev/null

mkdir -p "$runtime_bin_dir"
cp "$dist_bin" "$runtime_bin_dir/masking_engine"
chmod +x "$runtime_bin_dir/masking_engine"
node "$repo_root/scripts/prepare_package_fingerprint.mjs" \
  --repo "$repo_root" \
  --record-engine-build "masking_runtime/bin/masking_engine"
echo "masking_engine copied to $runtime_bin_dir"

# Windows Release Test

## 1. Download Artifact Or Release Asset

1. Open the GitHub Actions workflow `Build Windows Tauri`.
2. Run it on the release branch.
3. Prefer the GitHub Release assets over Actions artifacts because artifact storage quota can be exhausted.
4. If Actions artifact upload is blocked by quota, rerun the workflow with:

```text
publish_release = true
release_tag = vX.Y.Z
```

5. For normal use, download `Nothing-<version>-windows-x64-setup.exe`.
6. For no-install verification, download `Nothing-<version>-windows-x64-portable.zip` and extract it on a Windows desktop.
7. If both artifact and release upload are unavailable, use the workflow log sections `Release package manifest` and `Windows packaged desktop launch smoke` as temporary evidence, then rerun after quota is freed to perform the desktop download test.

Current release target:

- Tag: `v<version>`
- Source: release tag target commit
- Windows workflow run: record after `Build Windows Tauri` completes with `publish_release = true`
- Windows setup SHA-256: record from the workflow `Release package manifest` log
- Windows portable ZIP SHA-256: record from the workflow `Release package manifest` log
- macOS arm64 bundle SHA-256: `7d25f4cf243c585310980784df9b17cb561a198ce395338faf8a3ef1ea532ee6`
- Includes: Windows setup exe, Windows portable ZIP, Windows manifest, and macOS arm64 bundle.
- macOS signing: ad-hoc signed; `codesign --verify --deep --strict` passed on the downloaded release zip. The app is not Apple Developer ID notarized.

Last stabilization prerelease before merge:

- Tag: `windows-stabilization-2026-05-29-r3`
- Source: `refs/heads/codex/stabilize-local-redaction`
- Commit: `75e6d7d9bcae9136dcaf0a10eee223d15b98e0d8`
- Windows bundle SHA-256: `aa830c276a6985bc563f7dc550c1babf6026af7371331be0773cabb19ffd25fc`

Do not use `v2.0.1-mac-hotfix` to verify this Windows stabilization pass. That release is a historical macOS hotfix from `main` and does not contain the PR branch stabilization work.

## 2. Check Required Files

Confirm the Windows release assets contain:

- `Nothing-<version>-windows-x64-setup.exe`.
- `Nothing-<version>-windows-x64-portable.zip`.
- `Nothing-<version>-windows-x64-manifest.json`.

Confirm the extracted portable ZIP contains:

- `Nothing-<version>-windows-x64.exe`
- `masking_runtime/bin/masking_engine.exe`
- `masking_runtime/data/kr_regions.json`
- `masking_runtime/data/kr_regions.seed.json`
- `masking_runtime/tauri_frontend/scripts/run_masking_pipeline.py`
- `masking_runtime/tauri_frontend/scripts/apply_manual_boxes.py`

## 3. Run Smoke Test

From PowerShell in the repository checkout:

```powershell
.\scripts\e2e_windows_smoke.ps1
```

Expected output contains:

```text
WINDOWS_SMOKE_PASS
```

The smoke test creates only dummy values, does not print raw dummy values, and verifies:

- masked PDF exists
- safe_report exists
- safe_report does not contain raw dummy values
- extracted TXT is absent by default

## 4. Manual Desktop Test

1. Launch the Tauri app.
2. Select a non-sensitive internal test PDF.
3. Run masking with default `문서 + 안전 리포트`.
4. Confirm final status card and review queue.
5. Confirm no `.extracted.*.txt` file is created by default.
6. Confirm `safe_report` contains counts/tokens/status only.
7. Enable `비식별 TXT 함께 저장` once for each policy and confirm that only the
   transformed TXT is published: token, partial masking, and consistent pseudonym.

Do not use real personal information for release testing.

## 5. CI Desktop Launch Evidence

The workflow also runs:

```powershell
.\scripts\e2e_windows_desktop_smoke.ps1
```

This runs packaged manual-box PDF smoke through `masking_runtime/bin/masking_engine.exe`, verifies the separate setup exe exists, starts the packaged `Nothing-<version>-windows-x64.exe` from the assembled portable directory, verifies release-critical runtime files exist, waits for the app to stay alive for 8 seconds, and terminates it.

When `publish_release = true`, the workflow also runs `Windows release asset roundtrip smoke`. That step downloads the uploaded GitHub Release setup exe, portable ZIP, and manifest; extracts the portable ZIP into a fresh directory on the Windows runner; and starts the app from the downloaded release layout. This proves the published portable asset is runnable on the Windows runner; a user-controlled Windows desktop or VM remains the final acceptance environment.

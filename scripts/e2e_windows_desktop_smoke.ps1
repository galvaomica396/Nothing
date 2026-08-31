param(
  [string]$ReleaseDir = "release-windows\portable",
  [string]$InstallerPath = "release-windows\Nothing-4.6.3-windows-x64-setup.exe",
  [string]$WorkDir = "",
  [int]$StartupSeconds = 8
)

$ErrorActionPreference = "Stop"

function Resolve-RequiredPath {
  param(
    [string]$Path,
    [string]$Description
  )

  if (!(Test-Path $Path)) {
    throw "missing $Description`: $Path"
  }

  return (Resolve-Path $Path).Path
}

$releasePath = Resolve-RequiredPath -Path $ReleaseDir -Description "release directory"
$appExe = Resolve-RequiredPath -Path (Join-Path $releasePath "Nothing-4.6.3-windows-x64.exe") -Description "packaged Tauri executable"
$installerExe = Resolve-RequiredPath -Path $InstallerPath -Description "packaged Tauri NSIS installer"

$requiredRuntimeFiles = @(
  "masking_runtime\bin\masking_engine.exe",
  "masking_runtime\data\kr_regions.json",
  "masking_runtime\data\kr_regions.seed.json",
  "masking_runtime\document_masker_ocr_gui.py",
  "masking_runtime\public_detection.py",
  "masking_runtime\privacy_detection.py",
  "masking_runtime\masking_context.py",
  "masking_runtime\privacy_false_positive.py",
  "masking_runtime\privacy_spans.py",
  "masking_runtime\privacy_transformers.py",
  "masking_runtime\masking_rules.py",
  "masking_runtime\masking_extraction.py",
  "masking_runtime\masking_redaction.py",
  "masking_runtime\masking_reporting.py",
  "masking_runtime\path_guard.py",
  "masking_runtime\requirements.txt",
  "masking_runtime\tauri_frontend\scripts\run_masking_pipeline.py",
  "masking_runtime\tauri_frontend\scripts\apply_manual_boxes.py"
)

foreach ($relativePath in $requiredRuntimeFiles) {
  Resolve-RequiredPath -Path (Join-Path $releasePath $relativePath) -Description "runtime resource" | Out-Null
}

if ($WorkDir -eq "") {
  $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("makiiing-desktop-smoke-" + [System.Guid]::NewGuid().ToString("N"))
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$manualSmokeScript = Resolve-RequiredPath -Path (Join-Path $PSScriptRoot "e2e_manual_boxes_smoke.py") -Description "source manual boxes smoke script"
$enginePath = Resolve-RequiredPath -Path (Join-Path $releasePath "masking_runtime\bin\masking_engine.exe") -Description "packaged masking engine"

$manualStdout = & python $manualSmokeScript --workdir $WorkDir --engine-path $enginePath
if ($LASTEXITCODE -ne 0) {
  throw "[desktop-smoke] packaged manual boxes smoke failed"
}
$manualResult = $manualStdout | ConvertFrom-Json
if ($manualResult.status -ne "pass") {
  throw "[desktop-smoke] packaged manual boxes smoke did not pass"
}
Write-Host "[desktop-smoke] Packaged manual boxes PASS output_pdf=$($manualResult.output_pdf)"

Write-Host "[desktop-smoke] Starting packaged Windows desktop app."
$process = Start-Process -FilePath $appExe -WorkingDirectory $releasePath -PassThru

Start-Sleep -Seconds $StartupSeconds
$process.Refresh()

if ($process.HasExited) {
  throw "[desktop-smoke] packaged desktop app exited during startup window with code $($process.ExitCode)"
}

Write-Host "[desktop-smoke] Packaged desktop app stayed alive for $StartupSeconds seconds."

Stop-Process -Id $process.Id -Force
Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue

Write-Host "[desktop-smoke] PASS"

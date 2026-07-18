param(
  [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path,
  [string]$SpecPath = "",
  [string]$RuntimeBinDir = ""
)

$ErrorActionPreference = "Stop"

if ($SpecPath -eq "") {
  $SpecPath = Join-Path $RepoRoot "packaging\pyinstaller\masking_engine.spec"
}
if ($RuntimeBinDir -eq "") {
  $RuntimeBinDir = Join-Path $RepoRoot "masking_runtime\bin"
}

python -m PyInstaller --noconfirm $SpecPath

$distExe = Join-Path $RepoRoot "dist\masking_engine.exe"
if (!(Test-Path $distExe)) {
  throw "PyInstaller did not produce $distExe"
}

& $distExe --detector-smoke | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Packaged ko-pii detector smoke failed"
}

New-Item -ItemType Directory -Force -Path $RuntimeBinDir | Out-Null
Copy-Item $distExe (Join-Path $RuntimeBinDir "masking_engine.exe") -Force
Write-Host "masking_engine.exe copied to $RuntimeBinDir"

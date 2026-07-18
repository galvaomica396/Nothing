param(
  [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path,
  [string]$EnginePath = "",
  [string]$WorkDir = "",
  [string]$FixturePath = ""
)

$ErrorActionPreference = "Stop"

if ($WorkDir -eq "") {
  $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("makiiing-smoke-" + [System.Guid]::NewGuid().ToString("N"))
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$outDir = Join-Path $WorkDir "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$smokeScript = Join-Path $RepoRoot "scripts\e2e_fixture_smoke.py"
$smokeArgs = @($smokeScript, "--repo-root", $RepoRoot, "--workdir", $WorkDir)
if ($EnginePath -ne "") {
  $smokeArgs += @("--engine-path", $EnginePath)
}
if ($FixturePath -ne "") {
  $smokeArgs += @("--fixture", $FixturePath)
}

$stdout = & python @smokeArgs

if ($LASTEXITCODE -ne 0) {
  throw "fixture-backed masking smoke run failed"
}

$result = $stdout | ConvertFrom-Json
if ($result.status -ne "pass") {
  throw "fixture-backed masking smoke did not pass"
}

Write-Host "WINDOWS_SMOKE_PASS fixture=$($result.fixture_path) safe_report=$($result.safe_report_path) masked_pdf=$($result.masked_pdf_path) extracted_txt_default=absent"

<#
Monthly refresh on Windows: download new NHS England files, rebuild everything,
publish to the repo's data branch (Power BI reads it from there).

NHS England's website blocks cloud servers, so this runs on a normal PC.
  powershell -ExecutionPolicy Bypass -File scripts\refresh.ps1
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$log = Join-Path $repo "data\refresh.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Append | Out-Null
try {
    git pull --ff-only
    if (-not (Test-Path .venv)) { py -3 -m venv .venv }
    & .\.venv\Scripts\python.exe -m pip install --quiet -e .
    & .\.venv\Scripts\nhsp.exe all
    if ($LASTEXITCODE -ne 0) { throw "nhsp all failed ($LASTEXITCODE)" }
    & .\.venv\Scripts\nhsp.exe publish
    if ($LASTEXITCODE -ne 0) { throw "nhsp publish failed ($LASTEXITCODE)" }
}
finally {
    Stop-Transcript | Out-Null
}

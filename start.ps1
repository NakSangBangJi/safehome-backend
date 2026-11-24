param([int]$Port=8000,[switch]$LocalOnly)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 설치를 확인하세요.' }
}
& $python -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '라이브러리 설치에 실패했습니다.' }
$listenAddress = if ($LocalOnly) { '127.0.0.1' } else { '0.0.0.0' }
& $python run.py --host $listenAddress --port $Port

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    & py -3 factory_server.py
    exit $LASTEXITCODE
}
$candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python'
$found = Get-ChildItem -LiteralPath $candidate -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $found) {
    $found = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $found)) {
    throw 'Python 3 não encontrado. Instale Python 3.12+ e execute novamente.'
}
& $found factory_server.py
exit $LASTEXITCODE

param([Parameter(Mandatory=$true)][string]$DatasetRoot, [int]$Port=8000)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$runtimePython=Join-Path $PSScriptRoot '.runtime/Scripts/python.exe'
if(-not (Test-Path -LiteralPath $runtimePython)){throw 'Create the local environment first: python -m venv frontend-d/.runtime; then install fastapi uvicorn with its Python. See frontend-d/README.md.'}
$dataset=(Resolve-Path -LiteralPath $DatasetRoot).Path
if(-not (Test-Path -LiteralPath (Join-Path $dataset 'data'))){throw 'Dataset root must contain the data folder.'}
$previousData=$env:BLUEPRINTS_DATA
$previousCache=$env:BLUEPRINTS_CACHE
$previousBytecode=$env:PYTHONDONTWRITEBYTECODE
try{
 $env:BLUEPRINTS_DATA=$dataset
 $env:BLUEPRINTS_CACHE=Join-Path $PSScriptRoot '.cache'
 $env:PYTHONDONTWRITEBYTECODE='1'
 Push-Location $repoRoot
 & $runtimePython -m uvicorn serve:app --app-dir frontend-d --host 127.0.0.1 --port $Port
}finally{
 Pop-Location
 $env:BLUEPRINTS_DATA=$previousData
 $env:BLUEPRINTS_CACHE=$previousCache
 $env:PYTHONDONTWRITEBYTECODE=$previousBytecode
}

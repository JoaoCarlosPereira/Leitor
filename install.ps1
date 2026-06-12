# Wrapper Windows — delega para o instalador cross-platform em Python.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = $null
foreach ($candidate in @("python", "py", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    Write-Error "Python não encontrado. Instale Python 3.11+."
}

& $python install.py @args
exit $LASTEXITCODE

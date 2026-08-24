[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $projectRoot "main.py"
$configPath = Join-Path $projectRoot "config.windows.local.ini"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Write-Error "找不到本機設定檔：$configPath。為安全起見，不會退回使用 config.ini。"
    exit 1
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Error "找不到 .venv 的 Python。請先依 WINDOWS-INSTALL.md 建立 64-bit Python 3.11 虛擬環境。"
    exit 1
}

& $pythonPath -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and struct.calcsize('P') * 8 == 64 else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error ".venv 必須使用 64-bit Python 3.11。"
    exit 1
}

Push-Location $projectRoot
try {
    & $pythonPath $mainPath --config $configPath
    $applicationExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $applicationExitCode

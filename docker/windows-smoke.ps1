# Runs inside the Windows Server Core image after install.ps1 has completed.
# Verifies windows-curses, the real .cmd launcher, the user PATH entry, the
# headless flows, the test suite and uninstall/reinstall.
[CmdletBinding()]
param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Set-Location -LiteralPath $ProjectDir

$AppName = "fuzzy-surgical"
$VenvPython = Join-Path $ProjectDir "venv\Scripts\python.exe"
$InstallScript = Join-Path $ProjectDir "install.ps1"
$BaseDir = if ($env:LOCALAPPDATA) {
    $env:LOCALAPPDATA
} else {
    Join-Path $env:USERPROFILE "AppData\Local"
}
$BinDir = Join-Path $BaseDir "Programs\fuzzy-surgical"
$Launcher = Join-Path $BinDir "$AppName.cmd"

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw "FAIL: $Message"
    }
    Write-Host "ok  $Message"
}

function Assert-Python {
    param([string[]]$Arguments, [string]$Message)
    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "FAIL: $Message"
    }
    Write-Host "ok  $Message"
}

Write-Host "== Windows smoke =="
Write-Host (& $VenvPython --version | Out-String).Trim()
Assert-True (Test-Path -LiteralPath $VenvPython) "virtual environment exists"
Assert-Python @("-c", "import curses, pygments; print('curses and pygments import')") "windows-curses imports"

$versionFile = Join-Path $ProjectDir "fuzzy_surgical\__init__.py"
$versionMatch = Select-String -LiteralPath $versionFile -Pattern '__version__ = "([^"]+)"'
Assert-True ($null -ne $versionMatch) "version found in package"
$ExpectedVersion = $versionMatch.Matches[0].Groups[1].Value
$ActualVersion = (& $VenvPython (Join-Path $ProjectDir "main.py") --version | Out-String).Trim()
Assert-True ($ActualVersion -eq "$AppName $ExpectedVersion") "main.py reports $ActualVersion"

$help = (& $VenvPython (Join-Path $ProjectDir "main.py") --help | Out-String)
Assert-True ($help -like "*--no-lsp*") "command line help renders"

$HistoryProbe = @'
import os
from fuzzy_surgical.history import default_history_path

path = str(default_history_path())
base = os.environ["LOCALAPPDATA"]
assert path.lower().startswith(base.lower()), (path, base)
assert path.endswith("history.sqlite3"), path
print("history path:", path)
'@
$HistoryProbe | & $VenvPython -
if ($LASTEXITCODE -ne 0) {
    throw "FAIL: Windows history path"
}
Write-Host "ok  history database lives under LOCALAPPDATA"

$UriProbe = @'
import os
from fuzzy_surgical.lsp.protocol import path_to_uri, uri_to_path

source = os.path.abspath(os.path.join("fuzzy_surgical", "cli.py"))
uri = path_to_uri(source)
assert uri.startswith("file:///"), uri
round_trip = os.path.normcase(os.path.normpath(uri_to_path(uri)))
expected = os.path.normcase(os.path.normpath(source))
assert round_trip == expected, (round_trip, expected)
print("uri:", uri)
'@
$UriProbe | & $VenvPython -
if ($LASTEXITCODE -ne 0) {
    throw "FAIL: Windows file URI round trip"
}
Write-Host "ok  Windows drive letter file URIs round trip"

Write-Host "== Headless end-to-end =="
Assert-Python @((Join-Path $ProjectDir "scripts\headless_smoke.py")) "headless flows pass"

Write-Host "== Installed launcher =="
Assert-True (Test-Path -LiteralPath $Launcher) "launcher exists at $Launcher"
$MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$UserParts = @($UserPath -split ";" | Where-Object { $_ } | ForEach-Object { $_.TrimEnd("\") })
Assert-True ($UserParts -contains $BinDir.TrimEnd("\")) "user PATH contains $BinDir"

$env:Path = "$MachinePath;$UserPath"
$located = (& cmd /c "where $AppName" 2>&1 | Out-String).Trim()
Assert-True ($LASTEXITCODE -eq 0) "where finds $AppName"
Assert-True ($located -like "*$AppName.cmd*") "where resolves the generated cmd shim"

$LauncherVersion = (& cmd /c "call `"$Launcher`" --version" 2>&1 | Out-String).Trim()
Assert-True ($LauncherVersion -eq "$AppName $ExpectedVersion") "launcher reports $LauncherVersion"

$LauncherBytes = [System.IO.File]::ReadAllBytes($Launcher)
$HasBom = $LauncherBytes.Length -ge 3 -and $LauncherBytes[0] -eq 0xEF -and $LauncherBytes[1] -eq 0xBB -and $LauncherBytes[2] -eq 0xBF
Assert-True (-not $HasBom) "launcher has no UTF-8 BOM"
$OemEncoding = [System.Text.Encoding]::GetEncoding(
    [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage
)
$LauncherText = $OemEncoding.GetString($LauncherBytes)
Assert-True ($LauncherText.Contains("exit /b %ERRORLEVEL%")) "launcher propagates the exit code"
Assert-True ($LauncherText.Contains($VenvPython)) "launcher points at the project virtual environment"

$PathVersion = (& cmd /c "$AppName --version" 2>&1 | Out-String).Trim()
Assert-True ($PathVersion -eq "$AppName $ExpectedVersion") "PATH lookup runs $PathVersion"

$ModuleVersion = (& $VenvPython -m fuzzy_surgical --version | Out-String).Trim()
Assert-True ($ModuleVersion -eq "$AppName $ExpectedVersion") "python -m fuzzy_surgical reports $ModuleVersion"

Write-Host "== Test suite and lint =="
Assert-Python @("-m", "pytest", "-q", "tests") "pytest passes on Windows"
Assert-Python @("-m", "flake8", "main.py", "fuzzy_surgical", "tests", "scripts") "flake8 passes"

Write-Host "== Uninstall and reinstall =="
& powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $InstallScript -Uninstall
if ($LASTEXITCODE -ne 0) {
    throw "FAIL: uninstall.ps1 failed"
}
Assert-True (-not (Test-Path -LiteralPath $Launcher)) "uninstall removes the launcher"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Remaining = @($UserPath -split ";" | Where-Object { $_ })
Assert-True (-not ($Remaining -contains $BinDir)) "uninstall removes the PATH entry"

& powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $InstallScript
if ($LASTEXITCODE -ne 0) {
    throw "FAIL: reinstall failed"
}
Assert-True (Test-Path -LiteralPath $Launcher) "reinstall restores the launcher"

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathEntries = @($UserPath -split ";" | Where-Object { $_ -eq $BinDir })
Assert-True ($PathEntries.Count -eq 1) "reinstall keeps a single PATH entry"

Write-Host ""
Write-Host "windows smoke passed"
Write-Host "For an interactive TUI check, run on the Windows host:"
Write-Host "  docker run --rm -it fuzzy-surgical-test:windows powershell -NoLogo -NoProfile"
Write-Host "  cd C:\src"
Write-Host "  $AppName C:\src"

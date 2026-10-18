# Installs fuzzy-surgical for native Windows PowerShell / cmd terminals.
# Usage: powershell -ExecutionPolicy Bypass -File .\install.ps1
#        powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"

$AppName = "fuzzy-surgical"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir "venv"
$BaseDir = if ($env:LOCALAPPDATA) {
    $env:LOCALAPPDATA
} else {
    Join-Path $env:USERPROFILE "AppData\Local"
}
$BinDir = Join-Path $BaseDir "Programs\fuzzy-surgical"
$Launcher = Join-Path $BinDir "$AppName.cmd"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathParts = @($UserPath -split ";" | Where-Object { $_ })

if ($Purge) {
    $Uninstall = $true
}

function Remove-FromUserPath {
    param([string]$Directory)
    $remaining = @($PathParts | Where-Object { $_ -ne $Directory })
    [Environment]::SetEnvironmentVariable("Path", ($remaining -join ";"), "User")
}

if ($Uninstall) {
    if (Test-Path $Launcher) {
        Remove-Item $Launcher -Force
    }
    Remove-FromUserPath -Directory $BinDir
    if ($Purge -and (Test-Path $VenvDir)) {
        Remove-Item $VenvDir -Recurse -Force
    }
    Write-Host "Removed $Launcher"
    if ($Purge) {
        Write-Host "Removed $VenvDir"
    }
    exit 0
}

function Test-PythonCandidate {
    param([hashtable]$Candidate)
    $arguments = @($Candidate.Args) + @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
    )
    & $Candidate.Exe @arguments > $null 2>&1
    return ($LASTEXITCODE -eq 0)
}

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
            continue
        }
        if (Test-PythonCandidate -Candidate $candidate) {
            return $candidate
        }
    }
    return $null
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $Python = Find-Python
    if ($null -eq $Python) {
        throw "Python 3.9 or newer was not found. Install it from python.org first."
    }
    Write-Host "Creating virtual environment in $VenvDir"
    if ($Python.Args.Count -gt 0) {
        & $Python.Exe $Python.Args[0] -m venv $VenvDir
    } else {
        & $Python.Exe -m venv $VenvDir
    }
    if ($LASTEXITCODE -ne 0) {
        throw "The virtual environment was not created."
    }
    if (-not (Test-Path $VenvPython)) {
        throw "The virtual environment was not created."
    }
}

& $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "The virtual environment is older than Python 3.9. Remove $VenvDir and try again."
}

Write-Host "Installing dependencies"
& $VenvPython -m pip install --upgrade pip | Out-Null
& $VenvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

$PreviousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $VenvPython -c "import curses" 2>$null
$CursesExit = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorAction
if ($CursesExit -ne 0) {
    throw "curses is unavailable. Reinstall with: $VenvPython -m pip install windows-curses"
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$LauncherContent = @"
@echo off
"$VenvPython" "$ProjectDir\main.py" %*
exit /b %ERRORLEVEL%
"@
# cmd.exe reads batch files with the console (OEM) code page. Encoding.Default
# is ANSI in PowerShell 5.1 and UTF-8 in PowerShell 7, so either value mangles
# launchers for profiles whose path contains non-ASCII characters.
$OemCodePage = [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage
$LauncherEncoding = [System.Text.Encoding]::GetEncoding($OemCodePage)
$LauncherBytes = $LauncherEncoding.GetBytes($LauncherContent)
if ($LauncherEncoding.GetString($LauncherBytes) -ne $LauncherContent) {
    throw "The install path has characters cmd.exe cannot execute: $ProjectDir. Move the project to an ASCII path and run install.ps1 again."
}
[System.IO.File]::WriteAllBytes($Launcher, $LauncherBytes)
if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "The launcher was not written to $Launcher"
}

# A shim that exists but cannot start the app is worse than a failed install,
# so run it once before reporting success.
$LauncherCheck = (& $Launcher --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $LauncherCheck -notlike "$AppName *") {
    throw "The launcher at $Launcher did not run. Output: $LauncherCheck"
}
Write-Host "Verified launcher: $LauncherCheck"

$CurrentParts = @([Environment]::GetEnvironmentVariable("Path", "User") -split ";" | Where-Object { $_ })
if ($CurrentParts -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable(
        "Path",
        (($CurrentParts + $BinDir) -join ";"),
        "User"
    )
}

if (-not (($env:Path -split ";") -contains $BinDir)) {
    $env:Path = "$env:Path;$BinDir"
}

Write-Host ""
Write-Host "Installed $Launcher"
Write-Host "Project: $ProjectDir"
Write-Host ""
Write-Host "Open a new terminal and run:"
Write-Host "  $AppName"
Write-Host "The current PowerShell session can already use the command."

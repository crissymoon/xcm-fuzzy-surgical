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

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Exe = "py"; Args = @("-3") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Exe = "python"; Args = @() }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @{ Exe = "python3"; Args = @() }
    }
    return $null
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $Python = Find-Python
    if ($null -eq $Python) {
        throw "Python 3.8 or newer was not found. Install it from python.org first."
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
"@
[System.IO.File]::WriteAllText($Launcher, $LauncherContent, [System.Text.Encoding]::Default)

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

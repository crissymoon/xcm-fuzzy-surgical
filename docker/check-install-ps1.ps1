# Validates the PowerShell side of the harness without a Windows host.
# Parsing catches syntax errors on every script; the structural checks catch
# Windows guarantees that could be removed from install.ps1 by accident.
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

function Test-PowerShellFile {
    param([string]$File)
    $resolved = (Resolve-Path -LiteralPath $File).Path
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $resolved,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        foreach ($error in $errors) {
            Write-Host ("parse error in ${resolved}: " + $error.Message)
        }
        throw "$resolved does not parse"
    }
    Write-Host "ok  parsed $resolved"
}

$scripts = @(
    (Join-Path $Root "install.ps1"),
    (Join-Path $PSScriptRoot "windows-smoke.ps1"),
    (Join-Path $PSScriptRoot "run-tests.ps1")
)
foreach ($script in $scripts) {
    Test-PowerShellFile -File $script
}

$installer = Get-Content -Raw -LiteralPath (Join-Path $Root "install.ps1")
$required = @(
    "LOCALAPPDATA",
    "windows-curses",
    "SetEnvironmentVariable",
    '$AppName.cmd',
    "%*",
    "-Uninstall",
    "3, 9"
)
foreach ($needle in $required) {
    if (-not $installer.Contains($needle)) {
        throw "install.ps1 is missing expected text: $needle"
    }
}

Write-Host "install.ps1 keeps the Windows launcher, PATH and Python 3.9 checks"
Write-Host "PowerShell scripts validated with $($PSVersionTable.PSVersion)"

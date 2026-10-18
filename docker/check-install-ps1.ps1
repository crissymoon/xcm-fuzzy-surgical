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
        foreach ($problem in $errors) {
            Write-Host ("parse error in ${resolved}: " + $problem.Message)
        }
        throw "$resolved does not parse"
    }
    Write-Host "ok  parsed $resolved"
}

# Windows Server Core ships Windows PowerShell 5.1. PowerShell 7 accepts syntax
# such as ?? and ?: that 5.1 rejects, so parsing under pwsh is not enough.
# PSScriptAnalyzer's compatibility rule closes that gap on any host.
function Test-WindowsPowerShellSyntax {
    param([string]$File)

    if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
        Write-Host "skip PSScriptAnalyzer is not installed; only parsing was checked"
        return
    }
    Import-Module PSScriptAnalyzer -ErrorAction Stop
    $settings = @{
        Rules = @{
            PSUseCompatibleSyntax = @{
                Enable = $true
                TargetVersions = @("5.1")
            }
        }
    }
    $findings = @(
        Invoke-ScriptAnalyzer -Path $File -Settings $settings -IncludeRule @(
            "PSUseCompatibleSyntax",
            "PSAvoidAssignmentToAutomaticVariable"
        )
    )
    foreach ($finding in $findings) {
        Write-Host (
            "{0}:{1} {2} {3}" -f $File, $finding.Line, $finding.RuleName, $finding.Message
        )
    }
    if ($findings.Count -gt 0) {
        throw "$File is not compatible with Windows PowerShell 5.1"
    }
    Write-Host "ok  Windows PowerShell 5.1 compatible $File"
}

$scripts = @(
    (Join-Path $Root "install.ps1"),
    (Join-Path $PSScriptRoot "check-install-ps1.ps1"),
    (Join-Path $PSScriptRoot "windows-smoke.ps1"),
    (Join-Path $PSScriptRoot "run-tests.ps1")
)
foreach ($script in $scripts) {
    Test-PowerShellFile -File $script
    Test-WindowsPowerShellSyntax -File $script
}

$installer = Get-Content -Raw -LiteralPath (Join-Path $Root "install.ps1")
$required = @(
    "LOCALAPPDATA",
    "windows-curses",
    "SetEnvironmentVariable",
    '$AppName.cmd',
    "%*",
    "-Uninstall",
    "3, 9",
    "OEMCodePage",
    "WriteAllBytes",
    "exit /b %ERRORLEVEL%",
    "did not run"
)
foreach ($needle in $required) {
    if (-not $installer.Contains($needle)) {
        throw "install.ps1 is missing expected text: $needle"
    }
}

$runner = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "run-tests.ps1")
$runnerRequired = @("--isolation", "hyperv", "windows")
foreach ($needle in $runnerRequired) {
    if (-not $runner.Contains($needle)) {
        throw "run-tests.ps1 is missing expected text: $needle"
    }
}

$smoke = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "windows-smoke.ps1")
$smokeRequired = @("PATH lookup", "no UTF-8 BOM", "single PATH entry")
foreach ($needle in $smokeRequired) {
    if (-not $smoke.Contains($needle)) {
        throw "windows-smoke.ps1 is missing expected check: $needle"
    }
}

# Simulate the launcher encoding done by install.ps1. ASCII paths must round
# trip, and characters outside the code page must be detected instead of
# producing a shim that cmd.exe cannot execute.
function Test-LauncherEncoding {
    param([string]$ProjectPath, [int]$CodePage)
    $content = "@echo off`r`n`"$ProjectPath\venv\Scripts\python.exe`" `"$ProjectPath\main.py`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    $encoding = [System.Text.Encoding]::GetEncoding($CodePage)
    $bytes = $encoding.GetBytes($content)
    return ($encoding.GetString($bytes) -eq $content)
}

if (-not (Test-LauncherEncoding -ProjectPath "C:\projects\fuzzy-surgical" -CodePage 437)) {
    throw "an ASCII launcher path did not round trip through code page 437"
}
if (Test-LauncherEncoding -ProjectPath "C:\projects\fuzzy-surgical-日本語" -CodePage 437) {
    throw "the launcher encoding check missed an unrepresentable path"
}

Write-Host "install.ps1 keeps the Windows launcher, PATH and Python 3.9 checks"
Write-Host "run-tests.ps1 keeps isolation handling and the smoke checks"
Write-Host "launcher encoding round trips and rejects unrepresentable paths"
Write-Host "PowerShell scripts validated with $($PSVersionTable.PSVersion)"

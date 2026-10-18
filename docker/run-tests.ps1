# Builds and runs the Windows Server Core harness. Requires Windows with
# Docker Desktop switched to Windows containers.
[CmdletBinding()]
param(
    [ValidateSet("windows", "windows-shell")]
    [string]$Target = "windows",
    [ValidateSet("auto", "process", "hyperv")]
    [string]$Isolation = "auto",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Image = if ($env:FUZZY_SURGICAL_TEST_IMAGE) {
    $env:FUZZY_SURGICAL_TEST_IMAGE
} else {
    "fuzzy-surgical-test"
}
$Tag = "${Image}:windows"

function Resolve-Isolation {
    if ($Isolation -ne "auto") {
        return $Isolation
    }
    # ltsc2022 is build 20348. Process isolation only works when the host
    # build matches, so Windows 10 and 11 clients need Hyper-V isolation.
    $productType = (Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\ProductOptions").ProductType
    if ($productType -eq "WinNT") {
        return "hyperv"
    }
    return "process"
}

if ($env:OS -ne "Windows_NT") {
    throw "This harness needs a Windows host. Windows containers cannot run on macOS or Linux; use docker/run-tests.sh for the Linux harness."
}

Push-Location $Root
try {
    $OsType = (docker info --format '{{.OSType}}' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not reachable: $OsType"
    }
    if ($OsType -ne "windows") {
        throw "Docker is running Linux containers ($OsType). Switch Docker Desktop to Windows container mode."
    }

    $IsolationMode = Resolve-Isolation
    Write-Host "Using $IsolationMode isolation for $Tag"

    if (-not $NoBuild) {
        docker build --isolation $IsolationMode -f docker/Dockerfile.windows -t $Tag .
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed. On Windows 11 add -Isolation hyperv if it was not chosen automatically."
        }
    }

    $RunArguments = @("run", "--rm", "--isolation", $IsolationMode, $Tag)
    if ($Target -eq "windows-shell") {
        $RunArguments = @("run", "--rm", "-it", "--isolation", $IsolationMode, $Tag, "powershell", "-NoLogo", "-NoProfile")
    }

    & docker @RunArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Container run failed"
    }
} finally {
    Pop-Location
}

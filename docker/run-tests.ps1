# Builds and runs the Windows Server Core harness. Requires Windows with
# Docker Desktop switched to Windows containers.
[CmdletBinding()]
param(
    [ValidateSet("windows", "windows-shell")]
    [string]$Target = "windows"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Image = if ($env:FUZZY_SURGICAL_TEST_IMAGE) {
    $env:FUZZY_SURGICAL_TEST_IMAGE
} else {
    "fuzzy-surgical-test"
}

Push-Location $Root
try {
    $OsType = (docker info --format '{{.OSType}}' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $OsType -ne "windows") {
        throw "Docker is not running Windows containers. Switch Docker Desktop to Windows container mode."
    }

    docker build -f docker/Dockerfile.windows -t "${Image}:windows" .
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed"
    }

    if ($Target -eq "windows-shell") {
        docker run --rm -it "${Image}:windows" powershell -NoLogo -NoProfile
    } else {
        docker run --rm "${Image}:windows"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Container run failed"
    }
} finally {
    Pop-Location
}

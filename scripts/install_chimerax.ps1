[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$Sha256,

    [Parameter(Mandatory = $true)]
    [string]$InstallDir,

    [string]$InstallerPath,

    [string]$InnoUnpPath,

    [switch]$DownloadOnly
)

$ErrorActionPreference = "Stop"

$innoUnpUrl = "https://github.com/WhatTheBlock/innounp/releases/download/v0.50/innounp.exe"

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $env:TEMP "ChimeraX-$Version.exe"
}

$downloadFormFile = "$Version/windows/ChimeraX-$Version.exe"
$requestUri = "https://www.cgl.ucsf.edu/chimerax/cgi-bin/secure/chimerax-get.py"
$downloadUri = "https://www.rbvi.ucsf.edu/chimerax/cgi-bin/secure/chimerax-get.py"

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    $response = & curl.exe -s -F "file=$downloadFormFile" -F "choice=Accept" $requestUri
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to request ChimeraX installer token."
    }

    $match = [regex]::Match($response, "ident=([^&""']+)")
    if (-not $match.Success) {
        throw "Could not extract ChimeraX installer token from download response."
    }

    $ident = [uri]::UnescapeDataString($match.Groups[1].Value)
    & curl.exe -L -F "file=$downloadFormFile" -F "ident=$ident" -F "choice=Notified" $downloadUri -o $InstallerPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download ChimeraX installer."
    }
}

$actualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToUpperInvariant()
if ($actualHash -ne $Sha256.ToUpperInvariant()) {
    throw "Unexpected ChimeraX installer hash: $actualHash"
}

if ($DownloadOnly) {
    Write-Output $InstallerPath
    return
}

if (-not $InnoUnpPath) {
    $resolved = Get-Command innounp.exe -ErrorAction SilentlyContinue
    if ($resolved) {
        $InnoUnpPath = $resolved.Source
    } else {
        $InnoUnpPath = Join-Path $env:TEMP "innounp.exe"
        if (-not (Test-Path -LiteralPath $InnoUnpPath)) {
            Invoke-WebRequest -Uri $innoUnpUrl -OutFile $InnoUnpPath
        }
    }
}

if (-not (Test-Path -LiteralPath $InnoUnpPath)) {
    throw "innounp.exe was not found at $InnoUnpPath."
}

$existingConsolePath = Join-Path $InstallDir "ChimeraX-console.exe"
if (Test-Path -LiteralPath $existingConsolePath) {
    Write-Output $existingConsolePath
    return
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$extractRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cryoremote-chimerax-" + [guid]::NewGuid().ToString("N"))
$payloadRoot = Join-Path $extractRoot "{app}\bin"

try {
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    & $InnoUnpPath -x -b -y -q "-d$extractRoot" $InstallerPath *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "innounp failed with exit code $LASTEXITCODE."
    }

    $extractedConsolePath = Join-Path $payloadRoot "ChimeraX-console.exe"
    if (-not (Test-Path -LiteralPath $extractedConsolePath)) {
        throw "ChimeraX-console.exe was not found under $payloadRoot after extraction."
    }

    Get-ChildItem -LiteralPath $payloadRoot -Force | ForEach-Object {
        Move-Item -LiteralPath $_.FullName -Destination $InstallDir -Force
    }
} finally {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$consolePath = Join-Path $InstallDir "ChimeraX-console.exe"
if (-not (Test-Path -LiteralPath $consolePath)) {
    throw "ChimeraX-console.exe was not found under $InstallDir after extraction."
}

Write-Output $consolePath

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

$innoUnpZipUrl = "https://raw.githubusercontent.com/jrathlev/InnoUnpacker-Windows-GUI/refs/heads/master/innounp-2/bin/innounp-2.zip"
$innoUnpZipSha256 = "1439F8D9E24B19E7D0B31B9C427BA4533387522A370C39280F17D3371EB7FEBF"

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
        $innoUnpRoot = Join-Path $env:TEMP "innounp-2"
        $innoUnpZipPath = Join-Path $env:TEMP "innounp-2.zip"
        $InnoUnpPath = Join-Path $innoUnpRoot "innounp.exe"
        if (-not (Test-Path -LiteralPath $InnoUnpPath)) {
            if (Test-Path -LiteralPath $innoUnpRoot) {
                Remove-Item -LiteralPath $innoUnpRoot -Recurse -Force
            }
            & curl.exe -L $innoUnpZipUrl -o $innoUnpZipPath
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to download innounp zip."
            }
            $zipHash = (Get-FileHash -LiteralPath $innoUnpZipPath -Algorithm SHA256).Hash.ToUpperInvariant()
            if ($zipHash -ne $innoUnpZipSha256) {
                throw "Unexpected innounp zip hash: $zipHash"
            }
            Expand-Archive -LiteralPath $innoUnpZipPath -DestinationPath $innoUnpRoot -Force
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

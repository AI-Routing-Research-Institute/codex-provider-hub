param(
    [string]$Version = "0.1.0",
    [string]$DistDirectory = "dist"
)

$ErrorActionPreference = "Stop"

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use major.minor.patch format: $Version"
}

$projectPath = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$venvPython = Join-Path $projectPath ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    $python = $pythonCommand.Source
}

$sourceVersion = & $python -c "from codex_local_proxy_app import APP_VERSION; print(APP_VERSION)"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the application version"
}
if ($sourceVersion.Trim() -ne $Version) {
    throw "Source version $($sourceVersion.Trim()) does not match build version $Version"
}

$buildPath = Join-Path $projectPath ".build\local-proxy"
$workPath = Join-Path $buildPath "work"
$iconPath = Join-Path $buildPath "codex-local-proxy.ico"
$versionFile = Join-Path $buildPath "version-info.txt"
$distPath = if ([System.IO.Path]::IsPathRooted($DistDirectory)) {
    [System.IO.Path]::GetFullPath($DistDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectPath $DistDirectory))
}

New-Item -ItemType Directory -Force -Path $buildPath, $workPath, $distPath | Out-Null

& $python (Join-Path $projectPath "codex_local_proxy_app.py") --write-icon $iconPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "Unable to generate the executable icon"
}

$parts = $Version.Split('.') | ForEach-Object { [int]$_ }
$versionTuple = "$($parts[0]), $($parts[1]), $($parts[2]), 0"
$versionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($versionTuple),
    prodvers=($versionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [
          StringStruct('CompanyName', 'loongkkk'),
          StringStruct('FileDescription', 'Codex Local Proxy'),
          StringStruct('FileVersion', '$Version.0'),
          StringStruct('InternalName', 'CodexLocalProxy'),
          StringStruct('OriginalFilename', 'CodexLocalProxy-win-x64.exe'),
          StringStruct('ProductName', 'Codex Local Proxy'),
          StringStruct('ProductVersion', '$Version')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"@
[System.IO.File]::WriteAllText(
    $versionFile,
    $versionText,
    [System.Text.UTF8Encoding]::new($false)
)

$env:CODEX_LOCAL_PROXY_ICON = $iconPath
$env:CODEX_LOCAL_PROXY_VERSION_FILE = $versionFile

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $distPath `
    --workpath $workPath `
    (Join-Path $projectPath "packaging\CodexLocalProxy.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

$exePath = Join-Path $distPath "CodexLocalProxy-win-x64.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Build artifact was not found: $exePath"
}

$checksumPath = "$exePath.sha256"
$checksum = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    $checksumPath,
    "$checksum  $([System.IO.Path]::GetFileName($exePath))$([Environment]::NewLine)",
    [System.Text.UTF8Encoding]::new($false)
)

[pscustomobject]@{
    version = $Version
    executable = $exePath
    checksum = $checksumPath
    size_bytes = (Get-Item -LiteralPath $exePath).Length
    sha256 = $checksum
} | ConvertTo-Json

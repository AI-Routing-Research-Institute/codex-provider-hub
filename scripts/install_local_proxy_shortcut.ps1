param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ShortcutName = ("Codex " + (([char[]](0x672C, 0x5730, 0x4E2D, 0x8F6C)) -join "") + ".lnk")
)

$ErrorActionPreference = "Stop"

$projectPath = (Resolve-Path -LiteralPath $ProjectRoot).Path
$scriptPath = Join-Path $projectPath "local_proxy_app.py"
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "未找到本地中转入口：$scriptPath"
}

$pythonCandidates = @(
    (Join-Path $projectPath ".venv\Scripts\pythonw.exe"),
    (Join-Path $projectPath "venv\Scripts\pythonw.exe")
)
$pythonw = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $pythonw) {
    $pythonwCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($pythonwCommand) {
        $pythonw = $pythonwCommand.Source
    }
}
if (-not $pythonw) {
    throw "未找到 pythonw.exe，请先创建项目虚拟环境并安装 requirements-status.txt"
}

$python = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($pythonw), "python.exe")
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "未找到与 pythonw.exe 配套的 python.exe"
}

$iconDirectory = Join-Path $env:LOCALAPPDATA "CodexLocalProxy"
$iconPath = Join-Path $iconDirectory "codex-local-proxy.ico"
& $python $scriptPath --write-icon $iconPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "无法生成本地中转图标，请先安装 requirements-status.txt"
}
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop $ShortcutName
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $scriptPath + '" --tray --no-browser'
$shortcut.WorkingDirectory = $projectPath
$shortcut.Description = "Start Codex local proxy in the notification area"
$shortcut.IconLocation = $iconPath + ",0"
$shortcut.Save()

Write-Output $shortcutPath

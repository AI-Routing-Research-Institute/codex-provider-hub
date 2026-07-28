param(
    [string]$ShortcutPath = ''
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$GuiScript = Join-Path $Root 'probe_codex_gui.py'

if (-not (Test-Path -LiteralPath $GuiScript)) {
    throw "GUI 脚本不存在：$GuiScript"
}

$Pyw = (Get-Command pyw.exe -ErrorAction Stop).Source
if ([string]::IsNullOrWhiteSpace($ShortcutPath)) {
    $ShortcutPath = Join-Path (
        [Environment]::GetFolderPath('Desktop')
    ) 'Codex 供应商探测.lnk'
}

$ShortcutDirectory = Split-Path -Parent $ShortcutPath
if ($ShortcutDirectory -and (-not (Test-Path -LiteralPath $ShortcutDirectory))) {
    New-Item -ItemType Directory -Force -Path $ShortcutDirectory | Out-Null
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pyw
$Shortcut.Arguments = '-3 "' + $GuiScript + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,18"
$Shortcut.WindowStyle = 1
$Shortcut.Description = '选择 CC Switch 供应商和模型并执行 Codex 可用性探测'
$Shortcut.Save()

Write-Output $ShortcutPath

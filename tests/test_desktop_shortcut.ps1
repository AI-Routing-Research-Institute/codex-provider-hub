$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $root 'scripts\install_desktop_shortcut.ps1'
$guiScript = Join-Path $root 'probe_codex_gui.py'
$temporaryShortcut = Join-Path $env:TEMP 'Codex 供应商探测-test.lnk'

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

Assert-True (Test-Path -LiteralPath $installer) 'Shortcut installer is missing.'

$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $installer,
    [ref]$tokens,
    [ref]$parseErrors
) | Out-Null
Assert-True ($parseErrors.Count -eq 0) ('Installer has parse errors: ' + ($parseErrors | Out-String))

try {
    & $installer -ShortcutPath $temporaryShortcut | Out-Null
    Assert-True (Test-Path -LiteralPath $temporaryShortcut) 'Shortcut was not created.'

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($temporaryShortcut)
    Assert-True ($shortcut.TargetPath -like '*pyw.exe') 'Shortcut target should be pyw.exe.'
    Assert-True ($shortcut.Arguments -like '*probe_codex_gui.py*') 'Shortcut arguments should contain the GUI script.'
    Assert-True ($shortcut.WorkingDirectory -eq $root) 'Shortcut working directory is wrong.'
    Assert-True ($shortcut.IconLocation -like '*SHELL32.dll*') 'Shortcut icon should use a Windows system icon.'
} finally {
    Remove-Item -LiteralPath $temporaryShortcut -Force -ErrorAction SilentlyContinue
}

Write-Host 'desktop shortcut test passed'

# Claude 使用状況ウィジェットを Windows のスタートアップに登録して起動する
$ErrorActionPreference = 'Stop'
# 同じフォルダに exe（リリース版）があればそれを、なければ pythonw で .pyw を起動する
$exe = Join-Path $PSScriptRoot 'ClaudeUsageWidget.exe'
if (Test-Path $exe) {
    $target = $exe
    $arguments = ''
} else {
    $script = Join-Path $PSScriptRoot 'claude_usage_widget.pyw'
    $target = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $target) { $target = Join-Path (Split-Path (Get-Command python.exe).Source) 'pythonw.exe' }
    $arguments = '"' + $script + '"'
}

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude 使用状況.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $target
$sc.Arguments = $arguments
$sc.WorkingDirectory = $PSScriptRoot
$sc.Description = 'Claude 使用状況ウィジェット'
$sc.Save()

if ($arguments) { Start-Process $target -ArgumentList $arguments } else { Start-Process $target }
Write-Host "スタートアップに登録しました: $lnk"

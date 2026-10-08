# スタートアップ登録を解除し、起動中のウィジェットを終了する
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude 使用状況.lnk'
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "スタートアップから削除しました" }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*claude_usage_widget*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-Process ClaudeUsageWidget -ErrorAction SilentlyContinue | Stop-Process -Force

param([int]$Hour = 3, [int]$Minute = 0)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$label = "WorkLedger automatic backup"
$root = $env:WORKLEDGER_BACKUP_DIR ?? ".\workledger-backups"
New-Item -ItemType Directory -Force -Path $root | Out-Null
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\backup.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($Hour).AddMinutes($Minute))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName $label -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $root ".workledger-auto-backup-installed") | Out-Null
Write-Host "Installed $label at $($Hour.ToString('00')):$($Minute.ToString('00')) local"

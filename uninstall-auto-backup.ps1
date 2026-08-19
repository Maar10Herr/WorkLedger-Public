$ErrorActionPreference = "Stop"
Unregister-ScheduledTask -TaskName "WorkLedger automatic backup" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed WorkLedger automatic backup"

<#
Schedule scripts\refresh.ps1 for 10:30 on the second Friday of each month
(NHS England publishes A&E and RTT statistics on the second Thursday).
  powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
Remove it again with: Unregister-ScheduledTask -TaskName "NHS performance refresh"
#>
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'refresh.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory (Split-Path $PSScriptRoot)
schtasks /Create /TN "NHS performance refresh" /SC MONTHLY /MO SECOND /D FRI /ST 10:30 `
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`"" /F | Out-Null
Set-ScheduledTask -TaskName "NHS performance refresh" -Action $action `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable) | Out-Null
Write-Output "Scheduled: second Friday of each month, 10:30 (runs later if the PC was off)."

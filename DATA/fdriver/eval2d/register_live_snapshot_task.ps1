# Register Windows scheduled task for the fdriver 2d live snapshot (run_live_snapshot.bat).
#   Trigger 1: Mon, Thu 07:00 from 2026-10-20 until 2026-11-20 (end of day)
#   Trigger 2: Mon 07:00 from 2026-11-23
# Missed runs (PC off) start when available. Runs only while the user is logged on.
# Usage:   powershell -ExecutionPolicy Bypass -File register_live_snapshot_task.ps1
# Remove:  Unregister-ScheduledTask -TaskName fdriver_2d_live_snapshot -Confirm:$false
$name = "fdriver_2d_live_snapshot"
$bat = Join-Path $PSScriptRoot "run_live_snapshot.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $PSScriptRoot
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Thursday -At ([datetime]"2026-10-20T07:00:00")
$t1.EndBoundary = "2026-11-20T23:59:59"
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At ([datetime]"2026-11-23T07:00:00")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $t1, $t2 -Settings $settings -Principal $principal `
    -Description "fdriver 2d live snapshot (prereg v2). Log: OneDrive INVESTMENT fdriver 2d live logs" -Force | Out-Null
Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

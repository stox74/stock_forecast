# Register Windows scheduled task for the fdriver 2g live snapshot (run_live_2g.bat).
#   Trigger 1: Mon, Thu 05:00 from 2026-10-20 until 2026-11-20 (end of day)
#   Trigger 2: Mon 05:00 from 2026-11-23
# 2d task (fdriver_2d_live_snapshot) runs at 07:00. Execution limit 3 hours. Missed runs start when available.
# Usage:   powershell -ExecutionPolicy Bypass -File register_live_2g_task.ps1
# Remove:  Unregister-ScheduledTask -TaskName fdriver_2g_live_snapshot -Confirm:$false
$name = "fdriver_2g_live_snapshot"
$bat = Join-Path $PSScriptRoot "run_live_2g.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $PSScriptRoot
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Thursday -At ([datetime]"2026-10-20T05:00:00")
$t1.EndBoundary = "2026-11-20T23:59:59"
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At ([datetime]"2026-11-23T05:00:00")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $t1, $t2 -Settings $settings -Principal $principal `
    -Description "fdriver 2g live snapshot (method_2g_v1). Log: OneDrive INVESTMENT fdriver 2g live logs" -Force | Out-Null
Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime

[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'NekoCrane-FpsSkinIntel-Weekly')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$entryPoint = Join-Path $PSScriptRoot 'run_weapon_weekly.py'
if ((Get-TimeZone).Id -ne 'China Standard Time') {
    throw 'This task expects the Windows time zone China Standard Time (Monday 11:00 Beijing time).'
}
$pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and (($existing.Actions.Arguments -join ' ') -notlike '*run_weapon_weekly.py*')) {
    throw 'A task with this name already exists and is not owned by this pipeline.'
}
$taskAction = New-ScheduledTaskAction -Execute $pythonLauncher -Argument ('-3 -X utf8 "{0}" --publish' -f $entryPoint) -WorkingDirectory $projectRoot
$taskTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At '11:00'
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
if ($PSCmdlet.ShouldProcess($TaskName, 'Register the weekly Python skin pipeline')) {
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Collect weapon skins and official news, validate and publish without invoking an LLM.' -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, @{n='Command';e={$_.Actions.Execute}}, @{n='Arguments';e={$_.Actions.Arguments}}
    Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastTaskResult
}

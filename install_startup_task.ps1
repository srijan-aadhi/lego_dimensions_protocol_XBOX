<#
.SYNOPSIS
    Start the Lego Dimensions desk lamp automatically at log-on.

.DESCRIPTION
    Registers a Task Scheduler task that runs desk_lamp.py hidden (pythonw.exe)
    as soon as you log on, in rainbow mode with a 60-second lap and a log file
    in %LOCALAPPDATA%\LegoLamp. desk_lamp.py itself waits for the pad if USB is
    not ready yet, so no start delay is needed.

    The task runs only while you are logged on: a task that runs in the
    background "whether the user is logged on or not" cannot reach USB devices
    reliably.

.EXAMPLE
    .\install_startup_task.ps1            # register (or update) and start the task now
    .\install_startup_task.ps1 -Uninstall # remove the task and turn the lamp off
    .\install_startup_task.ps1 -Arguments '--rainbow -t 60 -b 50'   # different lamp options
    .\install_startup_task.ps1 -DelaySeconds 30                      # wait after log-on before starting

    If PowerShell refuses to run the script:
    powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
#>
param(
    [switch]$Uninstall,
    [string]$Arguments = '--rainbow -t 60',
    [string]$TaskName = 'Lego Dimensions Desk Lamp',
    [int]$DelaySeconds = 0,
    [string]$Python = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe')
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$script = Join-Path $here 'desk_lamp.py'
$pythonConsole = Join-Path (Split-Path $Python) 'python.exe'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        & $pythonConsole (Join-Path $here 'lamp_off.py')
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task '$TaskName'."
    } else {
        Write-Host "Task '$TaskName' is not registered."
    }
    return
}

if (-not (Test-Path $Python)) { throw "pythonw.exe not found at $Python (pass -Python)" }
if (-not (Test-Path $script)) { throw "desk_lamp.py not found next to this script" }

# --log = write the default log in %LOCALAPPDATA%\LegoLamp
$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "`"$script`" $Arguments --log" -WorkingDirectory $here

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
if ($DelaySeconds -gt 0) { $trigger.Delay = "PT${DelaySeconds}S" }

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -Priority 4   # normal priority; the default (7) is below normal and gets starved during log-on

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered task '$TaskName':"
Write-Host "  $Python `"$script`" $Arguments --log"
Write-Host "  runs at log-on (delay ${DelaySeconds}s); stop it with: python lamp_off.py"

# Task Scheduler writes PT0S for "no limit"; some Windows builds ignore it, so check.
$xml = [xml](Export-ScheduledTask -TaskName $TaskName)
$limit = $xml.Task.Settings.ExecutionTimeLimit
if ($limit -ne 'PT0S') { Write-Warning "Execution time limit is '$limit'; the lamp would be killed after that. Set it to PT0S in Task Scheduler." }

Start-ScheduledTask -TaskName $TaskName
Write-Host "Started the lamp."

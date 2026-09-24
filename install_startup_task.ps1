<#
.SYNOPSIS
    Start the Lego Dimensions desk lamp automatically at log-on.

.DESCRIPTION
    Registers a Task Scheduler task that runs desk_lamp.py hidden (pythonw.exe)
    30 seconds after you log on, in rainbow mode with a 20-second lap and a log
    file in %LOCALAPPDATA%\LegoLamp.

    The task runs only while you are logged on: a task that runs in the
    background "whether the user is logged on or not" cannot reach USB devices
    reliably.

.EXAMPLE
    .\install_startup_task.ps1            # register (or update) and start the task now
    .\install_startup_task.ps1 -Uninstall # remove the task and turn the lamp off
    .\install_startup_task.ps1 -Arguments '--rainbow -t 60 -b 50'   # different lamp options

    If PowerShell refuses to run the script:
    powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
#>
param(
    [switch]$Uninstall,
    [string]$Arguments = '--rainbow -t 20',
    [string]$TaskName = 'Lego Dimensions Desk Lamp',
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
$trigger.Delay = 'PT30S'   # give USB time to enumerate the pad

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered task '$TaskName':"
Write-Host "  $Python `"$script`" $Arguments --log"
Write-Host "  runs 30 s after log-on; stop it with: python lamp_off.py"

# Task Scheduler writes PT0S for "no limit"; some Windows builds ignore it, so check.
$xml = [xml](Export-ScheduledTask -TaskName $TaskName)
$limit = $xml.Task.Settings.ExecutionTimeLimit
if ($limit -ne 'PT0S') { Write-Warning "Execution time limit is '$limit'; the lamp would be killed after that. Set it to PT0S in Task Scheduler." }

Start-ScheduledTask -TaskName $TaskName
Write-Host "Started the lamp."

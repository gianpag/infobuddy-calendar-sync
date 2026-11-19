<#
.SYNOPSIS
Installs the InfoBuddy system tray app as a Windows Scheduled Task that starts at logon.

.DESCRIPTION
Shows the last sync status in the system tray with a "Sync Now" button. Safe to re-run:
it replaces any existing task with the same name. Uses pythonw.exe (no console window)
when available.

Run this from a normal PowerShell prompt (elevation not required, since this registers
a per-user task):

    powershell -ExecutionPolicy Bypass -File scheduling\windows\install_tray.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$PythonW = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$TrayScript = Join-Path $ProjectDir "tray_app.py"
$TaskName = "InfoBuddyTray"

if (-not (Test-Path $Python)) {
    Write-Error "$Python not found. Create the virtual environment and install requirements first (see README Quickstart)."
    exit 1
}

$Exe = if (Test-Path $PythonW) { $PythonW } else { $Python }

$Action = New-ScheduledTaskAction -Execute $Exe -Argument "`"$TrayScript`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
# No execution time limit -- this is a long-running tray app, not a one-shot job.
$Settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -ExecutionTimeLimit ([TimeSpan]::Zero)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "InfoMentor Extractor system tray status/trigger (InfoBuddy)" | Out-Null

Write-Host "Installed scheduled task '$TaskName'"
Write-Host "  Starts: automatically at logon"
Write-Host ""
Write-Host "Start it right now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Uninstall with:           Unregister-ScheduledTask -TaskName $TaskName"

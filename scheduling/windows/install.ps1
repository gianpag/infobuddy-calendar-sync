<#
.SYNOPSIS
Installs the InfoBuddy pipeline as a Windows Scheduled Task (runs 06:30 and 18:30 daily).

.DESCRIPTION
Safe to re-run: it replaces any existing task with the same name. Uses
run_pipeline.bat in this same directory as the actual entry point, since Scheduled
Tasks have no console and need output redirected to a log file.

Run this from a normal PowerShell prompt (elevation not required, since this
registers a per-user task):

    powershell -ExecutionPolicy Bypass -File scheduling\windows\install.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Wrapper = Join-Path $ScriptDir "run_pipeline.bat"
$TaskName = "InfoBuddyPipeline"

if (-not (Test-Path $Python)) {
    Write-Error "$Python not found. Create the virtual environment and install requirements first (see README Quickstart)."
    exit 1
}

$LogsDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$Action = New-ScheduledTaskAction -Execute $Wrapper -WorkingDirectory $ProjectDir
$Trigger1 = New-ScheduledTaskTrigger -Daily -At 6:30AM
$Trigger2 = New-ScheduledTaskTrigger -Daily -At 6:30PM
# StartWhenAvailable: if the PC was off/asleep at the scheduled time, run as soon as
# it's next available -- the closest Windows equivalent to launchd's deferred-wake
# behavior on macOS.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($Trigger1, $Trigger2) `
    -Settings $Settings -Description "InfoMentor extraction, Study Brief, and Calendar sync (InfoBuddy)" | Out-Null

Write-Host "Installed scheduled task '$TaskName'"
Write-Host "  Runs at: 06:30 and 18:30 daily"
Write-Host "  Logs:    $LogsDir\pipeline.out.log / pipeline.err.log"
Write-Host ""
Write-Host "Test it right now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Check status with:       Get-ScheduledTask -TaskName $TaskName"
Write-Host "Uninstall with:          Unregister-ScheduledTask -TaskName $TaskName"

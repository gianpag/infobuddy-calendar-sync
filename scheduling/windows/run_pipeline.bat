@echo off
REM Wrapper invoked by the Windows Scheduled Task (see install.ps1). Runs the pipeline
REM using the project's virtual environment and logs output, since a scheduled task has
REM no console to show it on.
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOGS_DIR=%PROJECT_DIR%\logs"

if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

cd /d "%PROJECT_DIR%"
"%PYTHON%" run_pipeline.py --headless --sync-calendar --sync-timetable >> "%LOGS_DIR%\pipeline.out.log" 2>> "%LOGS_DIR%\pipeline.err.log"

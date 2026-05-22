@echo off
echo =======================================
echo   PSUR Agent OS - Generation
echo =======================================
echo.

cd /d "%~dp0"

set /p START_DATE="Enter start date (YYYY-MM-DD): "
set /p END_DATE="Enter end date (YYYY-MM-DD): "

echo.
echo Period: %START_DATE% to %END_DATE%
echo.

REM Kill any zombie Python processes holding the venv lock
taskkill /f /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

REM Recreate venv if broken
if not exist .venv\Scripts\python.exe (
    uv venv
    uv pip install -r requirements.txt
)

echo.
echo Starting generation...
echo.
uv run main.py generate --start %START_DATE% --end %END_DATE%

echo.
echo =======================================
echo   Generation complete!
echo =======================================
pause

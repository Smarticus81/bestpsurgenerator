@echo off
echo =======================================
echo Starting PSUR Agent OS API Server...
echo =======================================

echo.
echo Installing requirements (this might take a moment if not already installed)...
uv pip install -r requirements.txt

echo.
echo Starting FastAPI server...
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

pause

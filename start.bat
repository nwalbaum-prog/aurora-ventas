@echo off
cd /d "%~dp0"
echo.
echo   Aurora Bakers ^— Sistema de Ventas
echo   =====================================
echo.

if not exist venv (
    echo   Primera vez: instalando dependencias...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo   ERROR: Python no encontrado.
        echo   Descargalo en https://python.org
        pause & exit /b 1
    )
    venv\Scripts\pip install flask --quiet
    echo   Dependencias instaladas.
    echo.
)

set GOOGLE_PLACES_API_KEY=AIzaSyA7_nd5CxsV22JmJfyhPedvxhVWAGxiBis

echo   Iniciando servidor...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000"
venv\Scripts\python app.py
pause

@echo off
cd /d "%~dp0"
echo.
echo   Aurora Bakers — servidor LOCAL (solo desarrollo)
echo   ADVERTENCIA: esta DB local NO se sincroniza con Railway.
echo   Produccion: https://aurora-ventas-production.up.railway.app
echo   =====================================
echo.

if not exist venv (
    echo   Primera vez: instalando dependencias...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo   ERROR: Python no encontrado.
        echo   Descargalo en https://python.org
        echo   Asegurate de marcar "Add Python to PATH" al instalar.
        pause & exit /b 1
    )
    venv\Scripts\pip install -r requirements.txt --quiet
    echo   Dependencias instaladas.
    echo.
)

echo   Iniciando servidor local...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000"
venv\Scripts\python app.py
pause

@echo off
REM ============================================================================
REM  Inicia SOLO el servidor de LinkedIn Summarizer (app independiente) y abre el
REM  dashboard. Libera el puerto 3002, arranca el servidor SIN ventana (pythonw,
REM  su propio venv) y abre http://localhost:3002.
REM ============================================================================
cd /d "%~dp0"

echo Liberando el puerto 3002 si estaba ocupado...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 3002 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Arrancando el servidor (sin ventana)...
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0src\server.py"

echo Esperando a que el servidor responda...
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
curl -s -o nul http://localhost:3002/api/status
if %errorlevel%==0 goto ready
set /a tries+=1
if %tries% geq 30 (
  echo No se pudo contactar con el servidor tras 30 s.
  pause
  exit /b 1
)
goto wait

:ready
echo Servidor listo. Abriendo el dashboard...
start "" "http://localhost:3002/"
timeout /t 2 /nobreak >nul

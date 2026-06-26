@echo off
REM ============================================================================
REM  Inicia LinkedIn Summarizer (app independiente) y lanza la cosecha.
REM  - Libera el puerto 3002 (mata un servidor anterior, para recargar codigo).
REM  - Arranca el servidor SIN ventana (pythonw, su propio venv).
REM  - Espera a que responda y abre el dashboard con ?harvest=1, que la extension
REM    detecta para disparar la cosecha automaticamente.
REM
REM  REQUISITOS: Chrome con la extension ya cargada, sesion de LinkedIn iniciada,
REM  y Chrome como navegador predeterminado (para que abra en ese perfil).
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
echo Servidor listo. Abriendo el dashboard y lanzando la cosecha...
start "" "http://localhost:3002/?harvest=1"
echo.
echo Listo. La cosecha se dispara desde Chrome (mira el popup de la extension).
echo Si no arranca: comprueba que la extension esta cargada y que tienes LinkedIn
echo abierto y con sesion iniciada en Chrome.
timeout /t 4 /nobreak >nul

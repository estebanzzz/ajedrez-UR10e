@echo off
REM Backend del robot ajedrecista, supervisado: si el proceso termina por
REM cualquier motivo (error, segfault de ur_rtde, cierre accidental), se
REM relanza a los 3 segundos. Camara y robot reconectan solos al volver.
REM Para detenerlo del todo: cerrar esta ventana.
cd /d "%~dp0"
:loop
echo [supervisor] iniciando backend...
".venv\Scripts\python.exe" run_vision.py
echo [supervisor] el backend termino (codigo %ERRORLEVEL%); reinicio en 3 s.
timeout /t 3 /nobreak >nul
goto loop

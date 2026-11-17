@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 30 --retries 2 -r setup-requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\setup_prebuilt.py %*
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Setup stopped. Keep the message above. No compiler or PlatformIO is required.
pause
exit /b 1

@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto run
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY goto run
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

echo Python not found. Install Python 3 from python.org and run again.
pause
exit /b 1

:run
%PY% ds_username.py
if errorlevel 1 pause

@echo off
rem Start the Zonic MCP server.  Usage:  run.bat [port]   (default 8300)
setlocal
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8300"
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%

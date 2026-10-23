@echo off
"%~dp0runtime\python.exe" -I -B -m scripts.portable %*
exit /b %errorlevel%

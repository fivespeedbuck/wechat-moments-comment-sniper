@echo off
cd /d "%~dp0\.."
where py >nul 2>nul
if errorlevel 1 (
  python src\moments_comment_sniper.py
) else (
  py src\moments_comment_sniper.py
)
if errorlevel 1 pause


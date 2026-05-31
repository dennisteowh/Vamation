@echo off
REM VAMA Integrated Pipeline - Auto-Update Launcher
REM Automatically updates the warehouse database with latest posts

echo ========================================
echo VAMA Integrated Pipeline - Auto-Update
echo ========================================
echo.

cd /d "%~dp0"

echo Starting pipeline...
echo.

python integrated_pipeline.py

echo.
echo ========================================
echo Pipeline execution completed
echo ========================================
echo.
pause

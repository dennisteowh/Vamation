@echo off
echo Starting VAMA Gallery Web Application...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.8+ and try again
    pause
    exit /b 1
)

REM Check if we're in the correct directory
if not exist "app.py" (
    echo Error: app.py not found
    echo Please run this script from the webapp directory
    pause
    exit /b 1
)

REM Check if metadata file exists
if not exist "..\metadata\posts_metadata.json" (
    echo Error: Metadata file not found at ..\metadata\posts_metadata.json
    echo Please ensure the VAMA Project structure is correct
    pause
    exit /b 1
)

REM Install requirements if they don't exist
echo Checking Python dependencies...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Error: Failed to install requirements
        pause
        exit /b 1
    )
)

echo.
echo =====================================
echo  VAMA Gallery Web Application
echo =====================================
echo.
echo Starting backend server...
echo The application will be available at:
echo http://127.0.0.1:5000
echo.
echo Press Ctrl+C to stop the server
echo.

REM Start the Flask application in background and open browser
start /B python app.py

REM Wait for server to start
timeout /t 3 /nobreak >nul

REM Open Chrome browser
start chrome http://127.0.0.1:5000

REM Wait for user to close
pause
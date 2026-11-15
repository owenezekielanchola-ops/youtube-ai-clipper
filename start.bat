@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   ClipForge AI - Starting Backend ^& Frontend
echo ==================================================

REM 1. Locate Python 3.10+ (checks PATH first, then common Windows install locations)
set "PYTHON_EXE="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
    )
)
if not defined PYTHON_EXE call :try_python "%LOCALAPPDATA%\Python\bin\python.exe"
if not defined PYTHON_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do if not defined PYTHON_EXE call :try_python "%%~fD\python.exe"
)
if not defined PYTHON_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PYTHON_EXE call :try_python "%%~fD\python.exe"
)
if not defined PYTHON_EXE (
    for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PYTHON_EXE call :try_python "%%~fD\python.exe"
)
if not defined PYTHON_EXE (
    for /d %%D in ("C:\Python3*") do if not defined PYTHON_EXE call :try_python "%%~fD\python.exe"
)
if not defined PYTHON_EXE (
    echo [ERROR] Python 3.10+ was not found on PATH or standard install folders.
    echo Install Python from https://www.python.org/downloads/ and check "Add Python to PATH".
    pause
    exit /b 1
)
for %%I in ("%PYTHON_EXE%") do set "PY_DIR=%%~dpI"
if exist "!PY_DIR!Scripts" set "PATH=!PY_DIR!Scripts;!PATH!"
set "PATH=!PY_DIR!;!PATH!"
echo [INFO] Using Python: %PYTHON_EXE%

REM 2. Locate Node.js / npm (checks PATH first, then standard / fnm / nvm locations)
set "NODE_DIR="
where npm >nul 2>&1
if errorlevel 1 (
    if exist "%ProgramFiles%\nodejs\npm.cmd" set "NODE_DIR=%ProgramFiles%\nodejs"
    if not defined NODE_DIR if exist "%ProgramFiles(x86)%\nodejs\npm.cmd" set "NODE_DIR=%ProgramFiles(x86)%\nodejs"
    if not defined NODE_DIR (
        for /d %%D in ("%APPDATA%\fnm\node-versions\*") do if exist "%%~fD\installation\npm.cmd" set "NODE_DIR=%%~fD\installation"
    )
    if not defined NODE_DIR (
        for /d %%D in ("%LOCALAPPDATA%\fnm\node-versions\*") do if exist "%%~fD\installation\npm.cmd" set "NODE_DIR=%%~fD\installation"
    )
    if not defined NODE_DIR (
        for /d %%D in ("%APPDATA%\nvm\v*") do if exist "%%~fD\npm.cmd" set "NODE_DIR=%%~fD"
    )
    if defined NODE_DIR set "PATH=!NODE_DIR!;!PATH!"
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js / npm was not found on PATH.
    echo Install Node.js 22+ LTS from https://nodejs.org/ ^(also used by yt-dlp for YouTube^).
    pause
    exit /b 1
)

REM 3. Check FFmpeg (system PATH, workspace_data\.bin shim, or imageio-ffmpeg)
if exist "%~dp0workspace_data\.bin\ffmpeg.exe" set "PATH=%~dp0workspace_data\.bin;!PATH!"
where ffmpeg >nul 2>&1
if errorlevel 1 (
    "%PYTHON_EXE%" -c "import imageio_ffmpeg" >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] FFmpeg was not found on PATH!
        echo           It will be installed via imageio-ffmpeg or you can run: winget install Gyan.FFmpeg
        echo.
    )
)

REM 4. Create backend\.env from .env.example if missing
if not exist "backend\.env" (
    if exist "backend\.env.example" (
        copy /y "backend\.env.example" "backend\.env" >nul
        echo [INFO] Created backend\.env from backend\.env.example.
        echo        Add your GROQ_API_KEY and GEMINI_API_KEY inside backend\.env.
    )
)

REM 5. Select existing Python environment or create backend\.venv
set "PYTHON_RUN="
if exist "backend\.venv\Scripts\python.exe" (
    set "PYTHON_RUN=%~dp0backend\.venv\Scripts\python.exe"
    "!PYTHON_RUN!" -c "import fastapi, uvicorn, yt_dlp, groq" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Installing missing backend Python dependencies...
        "!PYTHON_RUN!" -m pip install -r "backend\requirements.txt" imageio-ffmpeg
    )
) else (
    "%PYTHON_EXE%" -c "import fastapi, uvicorn, yt_dlp, groq" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_RUN=%PYTHON_EXE%"
        echo [INFO] Using system Python packages ^(all backend deps present^).
    ) else (
        echo [INFO] Creating Python virtual environment in backend\.venv...
        "%PYTHON_EXE%" -m venv "backend\.venv"
        if errorlevel 1 (
            echo [ERROR] Failed to create backend\.venv.
            pause
            exit /b 1
        )
        set "PYTHON_RUN=%~dp0backend\.venv\Scripts\python.exe"
        echo [INFO] Installing backend Python dependencies...
        "!PYTHON_RUN!" -m pip install --upgrade pip
        "!PYTHON_RUN!" -m pip install -r "backend\requirements.txt" imageio-ffmpeg
        if errorlevel 1 (
            echo [ERROR] Failed to install backend Python dependencies.
            pause
            exit /b 1
        )
    )
)

REM 6. Install frontend npm packages if needed
if not exist "frontend\node_modules" (
    echo [INFO] Installing frontend npm dependencies...
    pushd "frontend"
    call npm install
    if errorlevel 1 (
        popd
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
    popd
)

REM 7. Launch Backend and Frontend servers
echo [INFO] Launching FastAPI backend on http://localhost:8000 ...
start "ClipForge Backend (8000)" cmd /k "cd /d "%~dp0backend" && "%PYTHON_RUN%" -m uvicorn main:app --reload --port 8000"

echo [INFO] Launching Vite frontend on http://localhost:5173 ...
start "ClipForge Frontend (5173)" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo ==================================================
echo   ClipForge Studio is starting!
echo   Frontend UI:   http://localhost:5173
echo   Backend API:   http://localhost:8000/api/health
echo   API Keys file: backend\.env
echo ==================================================
echo.
endlocal
exit /b 0

:try_python
if not exist "%~1" exit /b 1
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON_EXE=%~1"
exit /b 0

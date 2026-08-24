@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=%~dp0"
set "VENV_DIR=%PROJECT_ROOT%.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "MAIN_PATH=%PROJECT_ROOT%main.py"
set "CONFIG_PATH=%PROJECT_ROOT%config.windows.local.ini"

if not "%~2"=="" goto :usage
if "%~1"=="" goto :arguments_ok
if /I "%~1"=="--check" goto :arguments_ok
goto :usage

:arguments_ok
if not exist "%PROJECT_ROOT%" (
    echo ERROR: Project directory not found: "%PROJECT_ROOT%"
    exit /b 1
)

if not exist "%VENV_DIR%\" (
    echo ERROR: Virtual environment directory not found: "%VENV_DIR%"
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python executable not found: "%PYTHON_EXE%"
    exit /b 1
)

if not exist "%MAIN_PATH%" (
    echo ERROR: Application entry point not found: "%MAIN_PATH%"
    exit /b 1
)

if not exist "%CONFIG_PATH%" (
    echo ERROR: Local configuration file not found: "%CONFIG_PATH%"
    echo ERROR: The launcher will not fall back to config.ini.
    exit /b 1
)

if /I "%~1"=="--check" (
    echo Windows launcher check OK
    exit /b 0
)

"%PYTHON_EXE%" -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and struct.calcsize('P') * 8 == 64 else 1)"
if errorlevel 1 (
    echo ERROR: The virtual environment must use 64-bit Python 3.11.
    exit /b 1
)

pushd "%PROJECT_ROOT%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Unable to enter project directory: "%PROJECT_ROOT%"
    exit /b 1
)

"%PYTHON_EXE%" "%MAIN_PATH%" --config "%CONFIG_PATH%"
set "APPLICATION_EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %APPLICATION_EXIT_CODE%

:usage
echo Usage: "%~nx0" [--check]
exit /b 2

@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "ENV_FILE=%SCRIPT_DIR%.env"
set "ENV_EXAMPLE=%SCRIPT_DIR%.env.example"
set "DEPLOYMENT_HELPER=%SCRIPT_DIR%scripts\deployment_config.py"

pushd "%SCRIPT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Unable to enter the project directory.
    exit /b 1
)

set "COMMAND=%~1"
if not defined COMMAND set "COMMAND=help"
shift

if /I "%COMMAND%"=="--check" goto :check
if /I "%COMMAND%"=="check" goto :check
if /I "%COMMAND%"=="help" goto :help
if /I "%COMMAND%"=="--help" goto :help
if /I "%COMMAND%"=="-h" goto :help

call :detect_compose
if errorlevel 1 goto :failure

if /I "%COMMAND%"=="create_env" (
    call :prepare_env
    goto :result
)
if /I "%COMMAND%"=="create_dirs" (
    call :create_dirs
    goto :result
)
if /I "%COMMAND%"=="up" (
    call :prepare_env
    if errorlevel 1 goto :failure
    call :run_compose up %*
    goto :result
)
if /I "%COMMAND%"=="down" (
    call :run_compose down %*
    goto :result
)
if /I "%COMMAND%"=="restart" (
    call :run_compose restart %*
    goto :result
)
if /I "%COMMAND%"=="status" (
    call :run_compose ps
    goto :result
)
if /I "%COMMAND%"=="logs" (
    call :run_compose logs %*
    goto :result
)
if /I "%COMMAND%"=="build" (
    call :run_compose build %*
    goto :result
)
if /I "%COMMAND%"=="pull" (
    call :run_compose pull %*
    goto :result
)
if /I "%COMMAND%"=="update" (
    call :prepare_env
    if errorlevel 1 goto :failure
    call :run_compose pull
    if errorlevel 1 goto :failure
    call :run_compose up -d
    goto :result
)
if /I "%COMMAND%"=="health" (
    call :run_compose exec -T ntrip-caster python /app/healthcheck.py
    goto :result
)
if /I "%COMMAND%"=="backup" (
    call :backup_data
    goto :result
)
if /I "%COMMAND%"=="info" goto :info
if /I "%COMMAND%"=="clean" (
    call :run_compose down --volumes --remove-orphans
    goto :result
)

echo ERROR: Unknown command: %COMMAND%
call :show_help
goto :failure

:check
if not exist "%SCRIPT_DIR%docker-compose.yml" (
    echo ERROR: docker-compose.yml was not found.
    goto :failure
)
if not exist "%SCRIPT_DIR%Dockerfile" (
    echo ERROR: Dockerfile was not found.
    goto :failure
)
if not exist "%ENV_EXAMPLE%" (
    echo ERROR: .env.example was not found.
    goto :failure
)
if not exist "%DEPLOYMENT_HELPER%" (
    echo ERROR: The deployment configuration helper was not found.
    goto :failure
)
echo Docker launcher check OK
goto :success

:help
call :show_help
goto :success

:info
echo NTRIP: port 2101 is published for remote clients by default.
echo Web: http://127.0.0.1:5757
echo Monitoring: http://127.0.0.1:9090 and http://127.0.0.1:3000
echo Credentials remain in the ignored local .env file and are not displayed.
goto :success

:result
if errorlevel 1 goto :failure
goto :success

:detect_compose
docker compose version >nul 2>&1
if not errorlevel 1 (
    set "COMPOSE_STYLE=plugin"
    exit /b 0
)
docker-compose --version >nul 2>&1
if not errorlevel 1 (
    set "COMPOSE_STYLE=standalone"
    exit /b 0
)
echo ERROR: Docker Compose was not found.
exit /b 1

:run_compose
if "%COMPOSE_STYLE%"=="plugin" (
    docker compose %*
) else (
    docker-compose %*
)
exit /b %ERRORLEVEL%

:prepare_env
if not exist "%ENV_EXAMPLE%" (
    echo ERROR: .env.example was not found.
    exit /b 1
)
if not exist "%DEPLOYMENT_HELPER%" (
    echo ERROR: The deployment configuration helper was not found.
    exit /b 1
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%DEPLOYMENT_HELPER%" prepare-env --env-file "%ENV_FILE%" --example "%ENV_EXAMPLE%" --monitoring
    if errorlevel 1 exit /b 1
    echo Docker credentials are stored in the ignored local .env file.
    exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3 is required to create Docker credentials safely.
    exit /b 1
)
python "%DEPLOYMENT_HELPER%" prepare-env --env-file "%ENV_FILE%" --example "%ENV_EXAMPLE%" --monitoring
if errorlevel 1 exit /b 1
echo Docker credentials are stored in the ignored local .env file.
exit /b 0

:create_dirs
for %%D in (data logs backup) do (
    if not exist "%SCRIPT_DIR%%%D\" mkdir "%SCRIPT_DIR%%%D"
)
exit /b 0

:backup_data
if not exist "%SCRIPT_DIR%data\" (
    echo ERROR: The data directory does not exist.
    exit /b 1
)
if not exist "%SCRIPT_DIR%backup\" mkdir "%SCRIPT_DIR%backup"
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "BACKUP_STAMP=%%T"
powershell -NoProfile -Command "Compress-Archive -LiteralPath '%SCRIPT_DIR%data' -DestinationPath '%SCRIPT_DIR%backup\ntrip_backup_%BACKUP_STAMP%.zip' -Force"
if errorlevel 1 exit /b 1
echo Backup completed in the local backup directory.
exit /b 0

:show_help
echo.
echo NTRIP Caster Docker launcher
echo.
echo Usage: "%~nx0" COMMAND [OPTIONS]
echo.
echo Commands:
echo   --check       Check launcher files without starting services
echo   create_env    Create a secure ignored .env file
echo   create_dirs   Create local data, logs, and backup directories
echo   up            Start services
echo   down          Stop services
echo   restart       Restart services
echo   status        Show service status
echo   logs          Show service logs
echo   build         Build the application image
echo   pull          Pull service images
echo   update        Pull and update services
echo   health        Run the container health check
echo   backup        Back up the local data directory
echo   info          Show default endpoints
echo   clean         Remove containers and named volumes
echo.
exit /b 0

:success
popd
exit /b 0

:failure
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
popd
exit /b %EXIT_CODE%

@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "ROOT=%~dp0"
for %%I in ("%ROOT%.") do set "ROOT=%%~fI"
cd /d "%ROOT%" || exit /b 1

set "NO_BROWSER=0"
set "SKIP_INSTALL=0"
set "SERVICE_MODE=0"
set "HELP=0"
set "PORT_ARG="
set "PORT_ARG_SET=0"

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="--no-browser" (
    set "NO_BROWSER=1"
    shift
    goto parse_args
)
if /I "%~1"=="--skip-install" (
    set "SKIP_INSTALL=1"
    shift
    goto parse_args
)
if /I "%~1"=="--service" (
    set "SERVICE_MODE=1"
    shift
    goto parse_args
)
if /I "%~1"=="--port" (
    if "%~2"=="" (
        echo --port requires a value 1>&2
        call :show_usage 1>&2
        exit /b 2
    )
    call :validate_port "%~2" PORT_ARG
    if errorlevel 1 (
        call :show_usage 1>&2
        exit /b 2
    )
    set "PORT_ARG_SET=1"
    shift
    shift
    goto parse_args
)
set "ARG=%~1"
if /I "%ARG:~0,7%"=="--port=" (
    call :validate_port "%ARG:~7%" PORT_ARG
    if errorlevel 1 (
        call :show_usage 1>&2
        exit /b 2
    )
    set "PORT_ARG_SET=1"
    shift
    goto parse_args
)
if /I "%~1"=="-h" (
    set "HELP=1"
    shift
    goto parse_args
)
if /I "%~1"=="--help" (
    set "HELP=1"
    shift
    goto parse_args
)
echo Unknown option: %~1 1>&2
call :show_usage 1>&2
exit /b 2

:after_args
if "%HELP%"=="1" (
    call :show_usage
    exit /b 0
)

if defined NIUONE_LOCAL_DATA_DIR (
    set "LOCAL_DATA_DIR=%NIUONE_LOCAL_DATA_DIR%"
) else (
    set "LOCAL_DATA_DIR=%ROOT%\.local-data"
)
if defined NIUONE_VENV_DIR (
    set "VENV_DIR=%NIUONE_VENV_DIR%"
) else (
    set "VENV_DIR=%LOCAL_DATA_DIR%\.venv"
)
if defined DASHBOARD_ENV_FILE (
    set "ENV_FILE=%DASHBOARD_ENV_FILE%"
) else (
    set "ENV_FILE=%LOCAL_DATA_DIR%\dashboard.env"
)

if not exist "%ENV_FILE%" (
    echo == First run: creating private runtime files ==
    call :create_default_env
    if errorlevel 1 exit /b 1
)

if "%PORT_ARG_SET%"=="1" (
    call :save_env_value DASHBOARD_PORT "%PORT_ARG%"
    if errorlevel 1 exit /b 1
    echo == Saved dashboard port to %ENV_FILE% ==
)

call :import_env

if not defined DASHBOARD_HOME set "DASHBOARD_HOME=%LOCAL_DATA_DIR%\runtime"
if not defined DASHBOARD_HOST set "DASHBOARD_HOST=127.0.0.1"
if not defined DASHBOARD_PORT set "DASHBOARD_PORT=8787"
set "DEFAULT_PYTHON_BIN=%VENV_DIR%\Scripts\python.exe"
if not defined PYTHON_BIN set "PYTHON_BIN=%DEFAULT_PYTHON_BIN%"
if not exist "%PYTHON_BIN%" set "PYTHON_BIN=%DEFAULT_PYTHON_BIN%"

mkdir "%DASHBOARD_HOME%\cron\output" >nul 2>nul
mkdir "%DASHBOARD_HOME%\logs" >nul 2>nul
mkdir "%LOCAL_DATA_DIR%" >nul 2>nul

set "VENV_CREATED=0"
if not exist "%PYTHON_BIN%" (
    echo == Creating Python virtual environment ==
    call :find_python_launcher
    if errorlevel 1 exit /b 1
    mkdir "%VENV_DIR%" >nul 2>nul
    call %PYTHON_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 exit /b 1
    set "PYTHON_BIN=%DEFAULT_PYTHON_BIN%"
    set "VENV_CREATED=1"
)

if "%SKIP_INSTALL%"=="1" (
    echo == Skipping dependency installation ==
) else (
    call :install_dependencies
    if errorlevel 1 exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\build-frontend.ps1" -Root "%ROOT%"
if errorlevel 1 exit /b 1

set "URL=http://%DASHBOARD_HOST%:%DASHBOARD_PORT%/"
set "DASHBOARD_ENV_FILE=%ENV_FILE%"
set "PYTHONDONTWRITEBYTECODE=1"
if not defined DASHBOARD_CONFIG set "DASHBOARD_CONFIG=%DASHBOARD_HOME%\config.yaml"
if not defined DASHBOARD_PUSH_HISTORY_DB set "DASHBOARD_PUSH_HISTORY_DB=%DASHBOARD_HOME%\push_history.db"
if not defined DASHBOARD_PORTFOLIO_STATE set "DASHBOARD_PORTFOLIO_STATE=%DASHBOARD_HOME%\cron\output\niuniu_practice_portfolio.json"
if not defined DASHBOARD_TRADER_SCRIPT set "DASHBOARD_TRADER_SCRIPT=%ROOT%\app\entrypoints\niuniu_practice_trader.py"

if "%SERVICE_MODE%"=="1" (
    where powershell.exe >nul 2>nul
    if errorlevel 1 (
        echo powershell.exe is required to install Windows scheduled tasks. 1>&2
        exit /b 1
    )
    echo == Installing NiuOne long-running scheduled tasks ==
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\manage-long-running.ps1" -Action Install -Root "%ROOT%" -Python "%PYTHON_BIN%" -LocalDataDir "%LOCAL_DATA_DIR%" -EnvFile "%ENV_FILE%"
    if errorlevel 1 exit /b 1
    echo == NiuOne is running in service mode ==
    echo   URL:        %URL%
    echo   data:       %DASHBOARD_HOME%
    echo   env:        %ENV_FILE%
    if "%NO_BROWSER%"=="0" (
        start "NiuOne Browser Opener" /min cmd /c "timeout /t 2 /nobreak >nul & start "" "%URL%""
    )
    exit /b 0
)

echo == Starting NiuOne Dashboard ==
echo   URL:        %URL%
echo   data:       %DASHBOARD_HOME%
echo   env:        %ENV_FILE%
echo   stop:       Ctrl+C

if "%NO_BROWSER%"=="0" (
    start "NiuOne Browser Opener" /min cmd /c "timeout /t 2 /nobreak >nul & start "" "%URL%""
)

"%PYTHON_BIN%" "%ROOT%\app\entrypoints\niuone_dashboard.py" --host "%DASHBOARD_HOST%" --port "%DASHBOARD_PORT%"
exit /b %ERRORLEVEL%

:show_usage
echo NiuOne one-click local runner for Windows
echo.
echo Usage:
echo   run.bat [options]
echo.
echo Options:
echo   --no-browser            Do not open the browser automatically
echo   --skip-install          Skip dependency installation
echo   --port VALUE            Save the dashboard port to dashboard.env before starting
echo   --service               Install and start long-running scheduled tasks
echo   -h, --help              Show this help
echo.
echo Environment:
echo   NIUONE_LOCAL_DATA_DIR  Private runtime directory, default: .local-data
echo   DASHBOARD_HOST         Dashboard host, default: 127.0.0.1
echo   DASHBOARD_PORT         Dashboard port, default: 8787
exit /b 0

:validate_port
set "PORT_CANDIDATE=%~1"
set "%~2="
if "%PORT_CANDIDATE%"=="" (
    echo --port must be an integer between 1 and 65535 1>&2
    exit /b 1
)
echo(%PORT_CANDIDATE%| findstr /R "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo --port must be an integer between 1 and 65535 1>&2
    exit /b 1
)
set /a PORT_NUMBER=%PORT_CANDIDATE% >nul 2>nul
if errorlevel 1 (
    echo --port must be an integer between 1 and 65535 1>&2
    exit /b 1
)
if %PORT_NUMBER% LSS 1 (
    echo --port must be an integer between 1 and 65535 1>&2
    exit /b 1
)
if %PORT_NUMBER% GTR 65535 (
    echo --port must be an integer between 1 and 65535 1>&2
    exit /b 1
)
set "%~2=%PORT_NUMBER%"
exit /b 0

:create_default_env
mkdir "%LOCAL_DATA_DIR%\runtime\cron\output" >nul 2>nul
mkdir "%LOCAL_DATA_DIR%\runtime\logs" >nul 2>nul
for %%I in ("%ENV_FILE%") do mkdir "%%~dpI" >nul 2>nul
set "DEFAULT_DASHBOARD_HOST=%DASHBOARD_HOST%"
if not defined DEFAULT_DASHBOARD_HOST set "DEFAULT_DASHBOARD_HOST=127.0.0.1"
set "DEFAULT_DASHBOARD_PORT=%DASHBOARD_PORT%"
if not defined DEFAULT_DASHBOARD_PORT set "DEFAULT_DASHBOARD_PORT=8787"
(
    echo # Generated by run.bat. Keep this file private.
    echo # Edit it directly or use /admin after the dashboard starts.
    echo DASHBOARD_HOME=%LOCAL_DATA_DIR%\runtime
    echo DASHBOARD_HOST=%DEFAULT_DASHBOARD_HOST%
    echo DASHBOARD_PORT=%DEFAULT_DASHBOARD_PORT%
    echo PYTHON_BIN=%VENV_DIR%\Scripts\python.exe
    echo.
    echo DASHBOARD_CONFIG=%LOCAL_DATA_DIR%\runtime\config.yaml
    echo DASHBOARD_PUSH_HISTORY_DB=%LOCAL_DATA_DIR%\runtime\push_history.db
    echo DASHBOARD_PORTFOLIO_STATE=%LOCAL_DATA_DIR%\runtime\cron\output\niuniu_practice_portfolio.json
    echo DASHBOARD_TRADER_SCRIPT=%ROOT%\app\entrypoints\niuniu_practice_trader.py
    echo.
    echo # The dashboard stays public; settings and admin APIs always require authentication.
    echo # Leave blank to use dashboard_admin_token.txt under DASHBOARD_HOME.
    echo DASHBOARD_ADMIN_PASSWORD=%DASHBOARD_ADMIN_PASSWORD%
) > "%ENV_FILE%"
exit /b %ERRORLEVEL%

:save_env_value
set "SAVE_NAME=%~1"
set "SAVE_VALUE=%~2"
for %%I in ("%ENV_FILE%") do mkdir "%%~dpI" >nul 2>nul
set "TMP_FILE=%ENV_FILE%.tmp.%RANDOM%%RANDOM%"
if exist "%ENV_FILE%" (
    > "%TMP_FILE%" (
        for /f "usebackq delims=" %%L in ("%ENV_FILE%") do (
            echo(%%L| findstr /B /C:"%SAVE_NAME%=" >nul
            if errorlevel 1 echo(%%L
        )
    )
) else (
    type nul > "%TMP_FILE%"
)
>> "%TMP_FILE%" <nul set /p "=%SAVE_NAME%=%SAVE_VALUE%"
>> "%TMP_FILE%" echo.
move /Y "%TMP_FILE%" "%ENV_FILE%" >nul
exit /b %ERRORLEVEL%

:import_env
if not exist "%ENV_FILE%" exit /b 0
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if not "%%~A"=="" set "%%A=%%B"
)
exit /b 0

:find_python_launcher
set "PYTHON_LAUNCHER="
python --version >nul 2>nul
if not errorlevel 1 set "PYTHON_LAUNCHER=python"
if not defined PYTHON_LAUNCHER (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
)
if not defined PYTHON_LAUNCHER (
    python3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=python3"
)
if not defined PYTHON_LAUNCHER (
    echo Python 3 is required but was not found in PATH. 1>&2
    exit /b 1
)
exit /b 0

:install_dependencies
set "REQ_HASH_FILE=%LOCAL_DATA_DIR%\.requirements.current.sha256"
"%PYTHON_BIN%" -c "from hashlib import sha256; from pathlib import Path; import sys; print(sha256(Path(sys.argv[1]).read_bytes()).hexdigest())" "%ROOT%\requirements.txt" > "%REQ_HASH_FILE%"
if errorlevel 1 exit /b 1
set "REQ_HASH="
set /p REQ_HASH=<"%REQ_HASH_FILE%"
del "%REQ_HASH_FILE%" >nul 2>nul

set "REQ_MARKER=%LOCAL_DATA_DIR%\.requirements.sha256"
set "INSTALLED_HASH="
if exist "%REQ_MARKER%" set /p INSTALLED_HASH=<"%REQ_MARKER%"

if "%VENV_CREATED%"=="1" goto do_install_dependencies
if not "%REQ_HASH%"=="%INSTALLED_HASH%" goto do_install_dependencies
echo == Python dependencies are up to date ==
exit /b 0

:do_install_dependencies
echo == Installing Python dependencies ==
"%PYTHON_BIN%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%PYTHON_BIN%" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 exit /b 1
> "%REQ_MARKER%" echo %REQ_HASH%
exit /b 0

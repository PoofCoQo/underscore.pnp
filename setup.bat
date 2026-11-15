@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv-win"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if /I "%~1"=="--help" goto help
if not "%~1"=="" goto unknown_argument

cd /d "%PROJECT_DIR%" || goto project_error

echo Setting up underscore.pnp. This may take a few minutes...
echo.

if exist "%VENV_PYTHON%" goto validate_venv
if exist "%VENV_DIR%" goto incomplete_venv

call :find_python
if errorlevel 1 goto python_missing

echo Creating Windows virtual environment in "%VENV_DIR%"...
"%PYTHON_LAUNCHER%" %PYTHON_SELECTOR% -m venv "%VENV_DIR%"
if errorlevel 1 goto setup_failed

:validate_venv
"%VENV_PYTHON%" -c "import struct,sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] < (3, 16) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto unsupported_venv

echo Updating pip...
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed

echo Installing underscore.pnp with Discord Voice support...
"%VENV_PYTHON%" -m pip install --editable ".[discord]"
if errorlevel 1 goto setup_failed

echo Checking installed dependencies...
"%VENV_PYTHON%" -m pip check
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -c "import discord, librosa, numpy, pyloudnorm, scipy, sounddevice, soundfile; from PySide6 import QtCore, QtQml; discord.opus.Encoder(); print('Python and native runtime checks passed.')"
if errorlevel 1 goto native_check_failed

echo.
echo Setup complete.
echo Start the player with:
echo   "%PROJECT_DIR%start.bat"
echo Configure the Discord bot token later in the app's Settings view.
echo.
if not defined CI pause
exit /b 0

:find_python
set "PYTHON_LAUNCHER="
set "PYTHON_SELECTOR="
where py >nul 2>nul
if not errorlevel 1 (
    py -3.15 -c "import struct; raise SystemExit(struct.calcsize('P') != 8)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=py"
        set "PYTHON_SELECTOR=-3.15"
        exit /b 0
    )
    py -3.14 -c "import struct; raise SystemExit(struct.calcsize('P') != 8)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=py"
        set "PYTHON_SELECTOR=-3.14"
        exit /b 0
    )
    py -3.13 -c "import struct; raise SystemExit(struct.calcsize('P') != 8)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=py"
        set "PYTHON_SELECTOR=-3.13"
        exit /b 0
    )
    py -3.12 -c "import struct; raise SystemExit(struct.calcsize('P') != 8)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=py"
        set "PYTHON_SELECTOR=-3.12"
        exit /b 0
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import struct,sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] < (3, 16) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=python"
        set "PYTHON_SELECTOR="
        exit /b 0
    )
)
exit /b 1

:help
echo Usage: setup.bat
echo.
echo Creates .venv-win and installs underscore.pnp with Discord Voice.
echo Requires 64-bit Python 3.12 through 3.15. The Discord token is configured
echo later in the app's Settings view.
exit /b 0

:unknown_argument
echo Unknown argument: %~1 1>&2
echo Run setup.bat --help for usage. 1>&2
exit /b 2

:project_error
echo Could not open the project directory: "%PROJECT_DIR%" 1>&2
goto failed_pause

:python_missing
echo Compatible Python was not found. 1>&2
echo Install 64-bit Python 3.12 through 3.15, then run setup.bat again. 1>&2
goto failed_pause

:incomplete_venv
echo The Windows virtual environment is incomplete: "%VENV_DIR%" 1>&2
echo Rename or remove that directory, then run setup.bat again. 1>&2
goto failed_pause

:unsupported_venv
echo .venv-win does not use supported 64-bit Python 3.12 through 3.15. 1>&2
echo Rename or remove "%VENV_DIR%", then run setup.bat again. 1>&2
goto failed_pause

:native_check_failed
echo A Python or native audio dependency could not be loaded. 1>&2
echo Review the error above. Ensure that 64-bit Python is installed. 1>&2
goto failed_pause

:setup_failed
echo Setup failed. Review the error above and run setup.bat again. 1>&2

:failed_pause
if not defined CI pause
exit /b 1

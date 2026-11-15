@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%.venv-win\Scripts\python.exe"

if not exist "%VENV_PYTHON%" goto not_configured
cd /d "%PROJECT_DIR%" || goto project_error

set "SPLASH_DIR="
set "PNP_MUSIC_SPLASH_DIR="
if "%PNP_MUSIC_NO_SPLASH%"=="1" goto launch_main
set "SPLASH_DIR=%TEMP%\underscore-pnp-splash-%RANDOM%-%RANDOM%"
if exist "%SPLASH_DIR%" goto no_splash
mkdir "%SPLASH_DIR%" >nul 2>&1 || goto no_splash
set "PNP_MUSIC_SPLASH_DIR=%SPLASH_DIR%"
start "" /B "%VENV_PYTHON%" -I "%PROJECT_DIR%src\pnp_music_player\ui\launcher_splash.py"
goto launch_main

:no_splash
set "SPLASH_DIR="

:launch_main
"%VENV_PYTHON%" -m pnp_music_player %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
if defined SPLASH_DIR rmdir "%SPLASH_DIR%" >nul 2>&1
if "%APP_EXIT_CODE%"=="0" exit /b 0

echo.
echo underscore.pnp exited with code %APP_EXIT_CODE%. 1>&2
if not defined CI pause
exit /b %APP_EXIT_CODE%

:not_configured
echo underscore.pnp is not set up for Windows yet. 1>&2
echo Run "%PROJECT_DIR%setup.bat" first. 1>&2
if not defined CI pause
exit /b 1

:project_error
echo Could not open the project directory: "%PROJECT_DIR%" 1>&2
if not defined CI pause
exit /b 1

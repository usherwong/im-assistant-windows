@echo off
setlocal
cd /d "%~dp0"

REM One-click local build + deploy. ASCII only: Chinese Windows cmd is GBK.
REM Build goes to dist\jazl (inside the repo); the runnable copy is deployed to
REM %JAZL_DEPLOY% (default D:\jazl) because an exe launched from inside the dev
REM workspace gets a low-integrity token and then cannot type into the chat app.

if "%JAZL_DEPLOY%"=="" set "JAZL_DEPLOY=D:\jazl"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtualenv .venv ...
    python -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail

echo Installing dependencies ...
python -m pip install -r requirements.txt pyinstaller || goto :fail

echo Building ...
pyinstaller --noconfirm --clean jev.spec || goto :fail

echo.
echo Deploying to %JAZL_DEPLOY% ...
if not exist "%JAZL_DEPLOY%" mkdir "%JAZL_DEPLOY%"
REM keep an existing config.json: it holds the provider choice and other settings
if exist "%JAZL_DEPLOY%\config.json" copy /y "%JAZL_DEPLOY%\config.json" "%TEMP%\jazl-config.json" >nul
robocopy "dist\jazl" "%JAZL_DEPLOY%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
if exist "%TEMP%\jazl-config.json" (
    copy /y "%TEMP%\jazl-config.json" "%JAZL_DEPLOY%\config.json" >nul
    del "%TEMP%\jazl-config.json" >nul
) else (
    if not exist "%JAZL_DEPLOY%\config.json" (
        > "%JAZL_DEPLOY%\config.json" echo {"jev_provider": "typesafe", "draft_provider": "deepseek"}
    )
)

echo.
echo Build OK.
echo   build : %cd%\dist\jazl\jazl.exe
echo   run   : %JAZL_DEPLOY%\jazl.exe
echo Data (config/roles/contacts) lives in %%LOCALAPPDATA%%\jazl
echo.
start "" "%JAZL_DEPLOY%\jazl.exe"
exit /b 0

:fail
echo.
echo Build FAILED. Scroll up for the error.
pause
exit /b 1

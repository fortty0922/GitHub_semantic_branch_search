@echo off
setlocal
cd /d "%~dp0"

if defined PYTHON (
    set "PY=%PYTHON%"
) else (
    py -3 -c "" >nul 2>&1 && (set "PY=py -3") || (
        python -c "" >nul 2>&1 && (set "PY=python") || (
            echo init: Python not found; install Python 3.11+ or set PYTHON 1>&2
            exit /b 1
        )
    )
)

%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
    echo init: Python 3.11+ required 1>&2
    %PY% -V
    exit /b 1
)

if not exist .venv (
    echo Creating .venv
    %PY% -m venv .venv
    if errorlevel 1 exit /b 1
)

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo init: .venv has no Python interpreter; delete .venv and run again 1>&2
    exit /b 1
)

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

if not exist .env (
    copy .env.example .env >nul
    echo Created .env; set GITHUB_TOKEN in it before crawling.
)

for %%F in (config\*.toml.example) do (
    if not exist "config\%%~nF" (
        copy "%%F" "config\%%~nF" >nul
        echo Created config\%%~nF from its example.
    )
)

echo.
echo Setup complete. Next:
echo   %VENV_PY% -m crawler check
echo   %VENV_PY% -m crawler fill        (or: run --hours 2)
echo   %VENV_PY% -m sbs corpus all
echo   %VENV_PY% -m pytest
echo   %VENV_PY% -m jupyter lab notebooks/corpus_review.ipynb
endlocal

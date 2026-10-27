@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ATLAS - Fabrica de Livros

set "TC="
set "TG="

rem --- 1) launcher "py -3" ------------------------------------------------
py -3 --version >nul 2>nul
if not errorlevel 1 (
    set "TC=py -3"
    set "TG=py -3"
    where pyw >nul 2>nul
    if not errorlevel 1 set "TG=pyw -3"
    call :try
)

rem --- 2) %LOCALAPPDATA%\Programs\Python\Python3*  (a versao mais nova primeiro)
if not defined PYCON (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do call :trydir "%LOCALAPPDATA%\Programs\Python\%%D"
)

rem --- 3) "python" no PATH, so se funcionar de verdade (alias da Microsoft Store nao conta)
if not defined PYCON (
    python --version >nul 2>nul
    if not errorlevel 1 (
        set "TC=python"
        set "TG=python"
        where pythonw >nul 2>nul
        if not errorlevel 1 set "TG=pythonw"
        call :try
    )
)

if not defined PYCON goto :nopython

start "" %PYGUI% atlas_gui.py %*
exit /b 0

:trydir
if defined PYCON exit /b 0
if not exist "%~1\python.exe" exit /b 0
set TC="%~1\python.exe"
set TG="%~1\python.exe"
if exist "%~1\pythonw.exe" set TG="%~1\pythonw.exe"
call :try
exit /b 0

:try
rem Aceita o Python candidato (TC/TG) se for 3.9+, tiver tkinter e importar a GUI.
%TC% -c "import sys, tkinter; assert sys.version_info >= (3, 9); import atlas_gui" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYCON=%TC%"
set "PYGUI=%TG%"
exit /b 0

:nopython
echo.
echo ============================================================
echo  ATLAS: nao encontrei um Python 3.9 ou mais novo com tkinter.
echo ============================================================
echo.
echo  O que fazer:
echo   1. Instale o Python 3 em https://www.python.org/downloads/
echo   2. No instalador, deixe marcado "tcl/tk and IDLE"
echo      (e, se quiser, "Add python.exe to PATH").
echo   3. Depois de instalar, de dois cliques em ATLAS.bat de novo.
echo.
echo  Observacao: os atalhos "python" e "python3" da Microsoft Store
echo  nao servem; o ATLAS ignora esses atalhos.
echo.
pause
exit /b 1

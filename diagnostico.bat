@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python nao encontrado.
  echo Instale o Python 3.11 ou mais novo em https://www.python.org/downloads/
  echo e marque a opcao "Add python.exe to PATH" durante a instalacao.
  echo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Preparando o ambiente pela primeira vez. Isso leva um ou dois minutos...
  python -m venv .venv
  if errorlevel 1 goto :erro
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :erro
if not exist ".env" copy /y ".env.example" ".env" >nul
".venv\Scripts\python.exe" -m radar diagnostico
echo.
pause
exit /b 0

:erro
echo.
echo Nao foi possivel preparar o ambiente. Confira a conexao com a internet e tente de novo.
pause
exit /b 1

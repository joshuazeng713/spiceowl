@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0" || exit /b 1

echo ==================================================
echo   SpiceOwl Connector 一键安装（仅需运行一次）
echo ==================================================
echo 将注册 spiceowl:// 协议并启动后台无界面连接器，
echo 之后只需在浏览器打开 https://spiceowl-dev.github.io/spiceowl/ 即可！
echo.

set "PY_CMD="
set "PY_ARG="
if exist ".venv\Scripts\python.exe" (
  set "PY_CMD=.venv\Scripts\python.exe"
  goto :run_install
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  for %%V in (3.14 3.13 3.12 3.11 3) do (
    py -%%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_CMD=py"
      set "PY_ARG=-%%V"
      goto :run_install
    )
  )
)
for %%C in (python3.exe python.exe) do (
  where %%C >nul 2>&1
  if not errorlevel 1 (
    %%C -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_CMD=%%C"
      goto :run_install
    )
  )
)

echo [SpiceOwl] 需要 Python 3.11+。请先安装：winget install Python.Python.3.12
pause
exit /b 1

:run_install
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if defined PY_ARG (
  "%PY_CMD%" %PY_ARG% -m spiceowl connector install
) else (
  "%PY_CMD%" -m spiceowl connector install
)
if errorlevel 1 (
  echo.
  echo [SpiceOwl] 安装未完成，请查看上方提示。
  pause
  exit /b 1
)
echo.
echo [SpiceOwl] 安装完成！正在为您打开正式网页工作台…
start "" "https://spiceowl-dev.github.io/spiceowl/"
echo 以后无需再双击此文件，直接访问网页点击「连接本机连接器」即可。
pause

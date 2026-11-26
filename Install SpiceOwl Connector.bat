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
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PY_CMD=.venv\Scripts\python.exe"
    goto :run_install
  )
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
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%ProgramFiles%\Python314\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%USERPROFILE%\anaconda3\python.exe"
) do (
  if exist %%P (
    %%P -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_CMD=%%~P"
      goto :run_install
    )
  )
)

echo ❌ [安装未完成] 需要 Python 3.11+。
echo    推荐安装：winget install -e --id Python.Python.3.12
echo    或官网下载：https://www.python.org/downloads/windows/
pause
exit /b 1

:run_install
echo [SpiceOwl] 已识别 Python (%PY_CMD% %PY_ARG%)，正在注册 spiceowl:// 协议并启动后台连接器…
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if defined PY_ARG (
  "%PY_CMD%" %PY_ARG% -m spiceowl connector install
) else (
  "%PY_CMD%" -m spiceowl connector install
)
if errorlevel 1 (
  echo.
  echo ❌ [安装失败] 请查看上方错误信息。
  pause
  exit /b 1
)
echo.
if defined PY_ARG (
  "%PY_CMD%" %PY_ARG% -m spiceowl connector status >nul 2>&1
) else (
  "%PY_CMD%" -m spiceowl connector status >nul 2>&1
)
if not errorlevel 1 (
  echo ✅ [安装并启动成功] 本机连接器已在后台运行 (http://127.0.0.1:8765/health)！
  start "" "https://spiceowl-dev.github.io/spiceowl/"
  echo 以后无需再双击此文件，直接访问网页点击「一键连接 Codex」或「一键识别我的 SSH 服务器」即可。
) else (
  echo ⚠️  协议已注册，但后台服务尚未就绪，请查看日志。
)
pause

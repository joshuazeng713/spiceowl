@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0" || exit /b 1

set "SRC_DIR=%~dp0"
if "%SRC_DIR:~-1%"=="\" set "SRC_DIR=%SRC_DIR:~0,-1%"
set "APP_HOME=%LOCALAPPDATA%\SpiceOwl"
set "VENV_DIR=%APP_HOME%\venv"
set "UV_DIR=%APP_HOME%\bin"
set "UV_EXE=%UV_DIR%\uv.exe"

echo ============================================================
echo   SpiceOwl Connector 全自动一键安装（Windows · 零环境配置）
echo ============================================================
echo 无需提前安装 Python，无需管理员权限。
echo 安装完成后，所有操作全部在网页端完成（安装包也可随时删除）。
echo.

echo [1/4] 准备独立 Python 3.11+ 运行环境…
if not exist "%APP_HOME%" mkdir "%APP_HOME%"

set "PY_CMD="
set "PY_ARG="
set "USE_UV="

if exist "%VENV_DIR%\Scripts\python.exe" (
  "%VENV_DIR%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PY_CMD=%VENV_DIR%\Scripts\python.exe"
    goto :setup_venv
  )
)
if exist "%SRC_DIR%\.venv\Scripts\python.exe" (
  "%SRC_DIR%\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PY_CMD=%SRC_DIR%\.venv\Scripts\python.exe"
    goto :setup_venv
  )
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  for %%V in (3.14 3.13 3.12 3.11 3) do (
    py -%%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_CMD=py"
      set "PY_ARG=-%%V"
      goto :setup_venv
    )
  )
)
for %%C in (python3.exe python.exe) do (
  where %%C >nul 2>&1
  if not errorlevel 1 (
    %%C -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_CMD=%%C"
      goto :setup_venv
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
      goto :setup_venv
    )
  )
)

echo       本机未检测到 Python 3.11+，正在自动下载轻量独立的 Python 3.12 运行时（免管理员）…
if not exist "%UV_DIR%" mkdir "%UV_DIR%"
where uv >nul 2>&1
if %ERRORLEVEL%==0 (
  set "USE_UV=uv"
  goto :bootstrap_uv
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_UNMANAGED_INSTALL='%UV_DIR%'; irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
if exist "%UV_EXE%" (
  set "USE_UV=%UV_EXE%"
  goto :bootstrap_uv
)
where winget >nul 2>&1
if %ERRORLEVEL%==0 (
  echo       尝试通过 winget 自动安装 Python 3.12（用户级）…
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :setup_venv
  )
)

echo ❌ [安装未完成] 自动下载 Python 3.12 失败（可能无网络连接）。
echo    请联网后重新双击，或从官网安装 Python 3.12：https://www.python.org/downloads/windows/
pause
exit /b 1

:bootstrap_uv
echo [2/4] 使用独立运行时创建永久虚拟环境并安装 SpiceOwl（%VENV_DIR%）…
"%USE_UV%" python install 3.12
"%USE_UV%" venv --python 3.12 --clear "%VENV_DIR%"
if errorlevel 1 (
  echo ❌ 创建独立虚拟环境失败，请检查网络后重试。
  pause
  exit /b 1
)
"%USE_UV%" pip install --python "%VENV_DIR%\Scripts\python.exe" --no-build-isolation --no-deps "%SRC_DIR%" >nul 2>&1
if errorlevel 1 (
  "%VENV_DIR%\Scripts\python.exe" -c "import shutil, sysconfig, pathlib; dst = pathlib.Path(sysconfig.get_path('purelib')) / 'spiceowl'; shutil.rmtree(dst, ignore_errors=True); shutil.copytree(pathlib.Path(r'%SRC_DIR%') / 'src' / 'spiceowl', dst)"
)
goto :ensure_pkg

:setup_venv
echo [2/4] 创建永久独立虚拟环境并安装 SpiceOwl（%VENV_DIR%）…
if not exist "%VENV_DIR%\Scripts\python.exe" (
  if defined PY_ARG (
    "%PY_CMD%" %PY_ARG% -m venv "%VENV_DIR%"
  ) else (
    "%PY_CMD%" -m venv "%VENV_DIR%"
  )
)
"%VENV_DIR%\Scripts\python.exe" -m pip install --no-deps --no-build-isolation --quiet "%SRC_DIR%" >nul 2>&1
if errorlevel 1 (
  "%VENV_DIR%\Scripts\python.exe" -c "import shutil, sysconfig, pathlib; dst = pathlib.Path(sysconfig.get_path('purelib')) / 'spiceowl'; shutil.rmtree(dst, ignore_errors=True); shutil.copytree(pathlib.Path(r'%SRC_DIR%') / 'src' / 'spiceowl', dst)"
)

:ensure_pkg
set "PERM_PY=%VENV_DIR%\Scripts\python.exe"

echo [3/4] 注册 spiceowl:// 协议并启动后台无界面连接器…
"%PERM_PY%" -m spiceowl connector install --restart
if errorlevel 1 (
  echo.
  echo ❌ [安装失败] 请查看上方错误信息。
  pause
  exit /b 1
)

echo [4/4] 核验本机连接器健康状态…
"%PERM_PY%" -m spiceowl connector status >nul 2>&1
if not errorlevel 1 (
  echo.
  echo ============================================================
  echo ✅ 安装并启动全部完成！（安装目录：%APP_HOME%）
  echo    • 后台连接器已就绪：http://127.0.0.1:8765/health
  echo    • spiceowl:// 唤醒协议与无黑框开机静默自启已自动配置
  echo    • 本次下载的压缩包和解压目录现在可以安全删除
  echo ============================================================
  start "" "https://spiceowl-dev.github.io/spiceowl/?connect=1#quickstart"
) else (
  echo ⚠️  协议已注册，但后台服务尚未就绪，请查看日志：%LOCALAPPDATA%\spiceowl\connector.log
)
echo.
pause

#!/bin/bash
# Zero-setup one-click installer for SpiceOwl Connector (macOS).
# Automatically prepares an isolated permanent Python 3.12 runtime (~/.local/share/spiceowl/venv),
# installs SpiceOwl into it (safe to delete the downloaded zip folder afterwards!),
# registers spiceowl:// + background autostart, starts the connector, verifies /health,
# and opens https://spiceowl-dev.github.io/spiceowl/.
cd -- "$(dirname -- "$0")" || exit 1
SRC_DIR="$(pwd)"
APP_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/spiceowl"
VENV_DIR="$APP_HOME/venv"
UV_BIN_DIR="$APP_HOME/bin"
UV_BIN="$UV_BIN_DIR/uv"

echo "============================================================"
echo "  🦉 SpiceOwl Connector 全自动一键安装（macOS · 零环境配置）"
echo "============================================================"
echo "无需提前安装 Python 或 Homebrew，无需管理员权限。"
echo "安装完成后，所有操作全部在网页端完成（安装包也可随时删除）。"
echo

find_python311() {
  local candidates=()
  candidates+=(
    "$VENV_DIR/bin/python"
    "$SRC_DIR/.venv/bin/python"
    "/opt/homebrew/bin/python3.14" "/opt/homebrew/bin/python3.13" "/opt/homebrew/bin/python3.12" "/opt/homebrew/bin/python3.11" "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3.14" "/usr/local/bin/python3.13" "/usr/local/bin/python3.12" "/usr/local/bin/python3.11" "/usr/local/bin/python3"
  )
  for pattern in \
    /opt/homebrew/opt/python@3.*/bin/python3* \
    /usr/local/opt/python@3.*/bin/python3* \
    /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
    "$HOME"/.pyenv/versions/3.*/bin/python3 \
    "$HOME"/miniconda*/bin/python3 \
    "$HOME"/anaconda*/bin/python3 \
    "$HOME"/miniforge*/bin/python3 \
    "$HOME"/mambaforge*/bin/python3 \
    "$HOME"/opt/anaconda*/bin/python3 \
    "$HOME"/.local/share/uv/python/*/bin/python3 \
    "$HOME"/.cache/codex-runtimes/*/dependencies/python/bin/python3; do
    if [ -x "$pattern" ]; then
      candidates+=("$pattern")
    fi
  done
  if command -v zsh >/dev/null 2>&1; then
    while IFS= read -r p; do
      [ -n "$p" ] && candidates+=("$p")
    done < <(zsh -lic 'which -a python3.14 python3.13 python3.12 python3.11 python3 2>/dev/null' 2>/dev/null || true)
  fi
  candidates+=(python3.14 python3.13 python3.12 python3.11 python3)

  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

ensure_uv() {
  if [ -x "$UV_BIN" ]; then
    echo "$UV_BIN"
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  echo "[1/4] 本机未检测到 Python 3.11+，正在自动下载轻量独立的 Python 3.12 运行时（免管理员、不影响系统）…"
  mkdir -p "$UV_BIN_DIR"
  if command -v curl >/dev/null 2>&1; then
    if env UV_UNMANAGED_INSTALL="$UV_BIN_DIR" sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null 2>&1; then
      if [ -x "$UV_BIN" ]; then
        echo "$UV_BIN"
        return 0
      fi
    fi
  fi
  return 1
}

echo "[1/4] 准备独立 Python 3.11+ 运行环境…"
mkdir -p "$APP_HOME"
USE_UV=""
PY_BIN="$(find_python311 || true)"

if [ -z "$PY_BIN" ]; then
  USE_UV="$(ensure_uv || true)"
  if [ -n "$USE_UV" ]; then
    "$USE_UV" python install 3.12
  elif [ -x "/opt/homebrew/bin/brew" ] || command -v brew >/dev/null 2>&1; then
    BREW_BIN="$(command -v brew 2>/dev/null || echo /opt/homebrew/bin/brew)"
    echo "      正在通过 Homebrew 自动安装 python@3.12…"
    "$BREW_BIN" install python@3.12
    PY_BIN="$(find_python311 || true)"
  fi
fi

echo "[2/4] 创建永久独立虚拟环境并安装 SpiceOwl (${VENV_DIR})…"
if [ -n "$USE_UV" ]; then
  if ! "$USE_UV" venv --python 3.12 --clear "$VENV_DIR"; then
    echo "❌ 创建独立虚拟环境失败，请检查网络连接后重试。"
    read -r -p "按回车关闭。"
    exit 1
  fi
  if ! "$USE_UV" pip install --python "$VENV_DIR/bin/python" --no-build-isolation --no-deps "$SRC_DIR" 2>/dev/null; then
    "$USE_UV" pip install --python "$VENV_DIR/bin/python" "$SRC_DIR"
  fi
else
  if [ -z "$PY_BIN" ]; then
    echo "❌ 自动准备 Python 3.12 失败（可能处于离线环境且本机无 Python 3.11+）。"
    echo "   请联网后重新双击，或安装 Python 3.12：https://www.python.org/downloads/macos/"
    read -r -p "按回车关闭。"
    exit 1
  fi
  PY_VER="$("$PY_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"
  echo "      使用本机 Python ${PY_VER} (${PY_BIN})"
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PY_BIN" -m venv "$VENV_DIR"
  fi
  # Fast zero-dependency install (works offline too!); upgrade/install package into permanent site-packages
  if ! "$VENV_DIR/bin/python" -m pip install --no-deps --no-build-isolation --quiet "$SRC_DIR" 2>/dev/null; then
    # Fallback: copy package cleanly into venv site-packages so offline installs without setuptools wheel also succeed!
    SITE_DIR="$("$VENV_DIR/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
    rm -rf "$SITE_DIR/spiceowl"
    cp -R "$SRC_DIR/src/spiceowl" "$SITE_DIR/spiceowl"
  fi
fi

PERM_PY="$VENV_DIR/bin/python"
if ! "$PERM_PY" -c 'import spiceowl' >/dev/null 2>&1; then
  SITE_DIR="$("$PERM_PY" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  rm -rf "$SITE_DIR/spiceowl"
  cp -R "$SRC_DIR/src/spiceowl" "$SITE_DIR/spiceowl"
fi

echo "[3/4] 注册 spiceowl:// 协议并启动后台无界面连接器…"
if ! "$PERM_PY" -m spiceowl connector install; then
  echo
  echo "❌ [安装失败] 请查看上方错误信息。"
  if [ -z "${SPICEOWL_NO_PAUSE:-}" ]; then read -r -p "按回车关闭。"; fi
  exit 1
fi

echo "[4/4] 核验本机连接器健康状态…"
if "$PERM_PY" -m spiceowl connector status >/dev/null 2>&1; then
  echo
  echo "============================================================"
  echo "✅ 安装并启动全部完成！(安装目录：${APP_HOME})"
  echo "   • 后台连接器已就绪：http://127.0.0.1:8765/health"
  echo "   • spiceowl:// 唤醒协议与开机静默常驻已自动配置"
  echo "   • 本次下载的压缩包和解压目录现在可以安全删除"
  echo "============================================================"
  echo "正在为您打开 SpiceOwl 正式网页工作台…"
  if [ -z "${SPICEOWL_NO_BROWSER:-}" ]; then
    open "https://spiceowl-dev.github.io/spiceowl/" 2>/dev/null || true
  fi
else
  echo
  echo "⚠️  协议已注册，但后台服务尚未就绪。请查看日志：~/.local/state/spiceowl/connector.log"
fi
echo
if [ -z "${SPICEOWL_NO_PAUSE:-}" ]; then
  read -r -p "按回车关闭此窗口（后台连接器会持续静默运行）。"
fi

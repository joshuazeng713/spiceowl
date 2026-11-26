#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1

echo "=================================================="
echo "  SpiceOwl Connector 一键安装（仅需运行一次）"
echo "=================================================="
echo "将注册 spiceowl:// 协议（免管理员权限）并启动后台无界面连接器，"
echo "之后只需在浏览器打开 https://spiceowl-dev.github.io/spiceowl/ 即可！"
echo

find_python311() {
  local candidates=()
  candidates+=(
    ".venv/bin/python"
    "/opt/homebrew/bin/python3.14" "/opt/homebrew/bin/python3.13" "/opt/homebrew/bin/python3.12" "/opt/homebrew/bin/python3.11" "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3.14" "/usr/local/bin/python3.13" "/usr/local/bin/python3.12" "/usr/local/bin/python3.11" "/usr/local/bin/python3"
  )
  # Homebrew keg-only Python, python.org Framework, Conda/Miniforge, Pyenv, uv, and Codex runtime Python
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
  # Also query user's login shell PATH (Finder double-click does not load ~/.zshrc)
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

PY_BIN="$(find_python311 || true)"

if [ -z "$PY_BIN" ]; then
  echo "[SpiceOwl] 未找到 Python 3.11+（系统自带的 /usr/bin/python3 版本过低）。"
  if [ -x "/opt/homebrew/bin/brew" ] || command -v brew >/dev/null 2>&1; then
    BREW_BIN="$(command -v brew 2>/dev/null || echo /opt/homebrew/bin/brew)"
    echo "检测到您已安装 Homebrew，是否立即自动安装 python@3.12？"
    read -r -p "按回车自动安装（或输入 n 取消）: " ans
    if [ "$ans" != "n" ] && [ "$ans" != "N" ]; then
      "$BREW_BIN" install python@3.12
      PY_BIN="$(find_python311 || true)"
    fi
  fi
fi

if [ -z "$PY_BIN" ]; then
  echo
  echo "❌ [安装未完成] 仍未检测到 Python 3.11+。"
  echo "   请安装 Python 3.12 后重新双击本脚本："
  echo "   方式 1：brew install python@3.12"
  echo "   方式 2：https://www.python.org/downloads/macos/"
  read -r -p "按回车关闭。"
  exit 1
fi

PY_VER="$("$PY_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"
echo "[SpiceOwl] 已识别 Python ${PY_VER} (${PY_BIN})"
echo "[SpiceOwl] 正在注册 spiceowl:// 协议并启动后台无界面连接器…"

export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
if ! "$PY_BIN" -m spiceowl connector install; then
  echo
  echo "❌ [安装失败] 请查看上方错误信息。"
  read -r -p "按回车关闭。"
  exit 1
fi

echo
if "$PY_BIN" -m spiceowl connector status >/dev/null 2>&1; then
  echo "✅ [安装并启动成功] 本机连接器已在后台运行 (http://127.0.0.1:8765/health)！"
  echo "正在为您打开正式网页工作台：https://spiceowl-dev.github.io/spiceowl/"
  open "https://spiceowl-dev.github.io/spiceowl/" 2>/dev/null || true
  echo "以后无需再双击此脚本，直接在网页点击「一键连接 Codex」或「一键识别我的 SSH 服务器」即可。"
else
  echo "⚠️  协议已注册，但后台服务尚未就绪。请检查日志 ~/.local/state/spiceowl/connector.log。"
fi
read -r -p "按回车关闭此安装窗口（后台连接器将继续运行）。"

#!/bin/bash
set -e
cd -- "$(dirname -- "$0")" || exit 1

echo "=================================================="
echo "  SpiceOwl Connector 一键安装（仅需运行一次）"
echo "=================================================="
echo "将注册 spiceowl:// 协议（免管理员权限）并启动后台无界面连接器，"
echo "之后只需在浏览器打开 https://spiceowl-dev.github.io/spiceowl/ 即可！"
echo

PY_BIN=""
for candidate in .venv/bin/python \
  /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
  /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3 \
  python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PY_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PY_BIN" ]; then
  echo "[SpiceOwl] 需要 Python 3.11+。推荐安装：brew install python@3.12"
  read -r -p "按回车关闭。"
  exit 1
fi

export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
"$PY_BIN" -m spiceowl connector install

echo
echo "[SpiceOwl] 安装完成！正在打开正式网页工作台…"
open "https://spiceowl-dev.github.io/spiceowl/" 2>/dev/null || true
echo "以后无需再双击此脚本，直接访问网页即可一键连接本机连接器。"
read -r -p "按回车关闭此安装窗口（后台连接器将继续运行）。"

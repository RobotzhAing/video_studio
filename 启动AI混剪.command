#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "缺少 .venv/bin/python，请先创建项目虚拟环境。"
  exit 1
fi
exec env VIDEOSTUDIO_PYTHON="$(pwd)/.venv/bin/python" npm start

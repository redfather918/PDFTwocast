#!/usr/bin/env bash
# ── PDFTwocast 启动脚本 ──
# 使用方法:
#   export DEEPSEEK_API_KEY=sk-...
#   export MINIMAX_API_KEY=...
#   export MINIMAX_GROUP_ID=...
#   bash start.sh

set -e
cd "$(dirname "$0")"

PYTHON="C:/Users/HUAWEI/.workbuddy/binaries/python/versions/3.13.12/python.exe"
VENV="C:/Users/HUAWEI/.workbuddy/binaries/python/envs/pdftwocast"

# 创建虚拟环境（如果不存在）
if [ ! -f "$VENV/Scripts/python.exe" ]; then
  echo "[setup] 创建虚拟环境..."
  "$PYTHON" -m venv "$VENV"
fi

# 安装依赖
echo "[setup] 安装依赖..."
"$VENV/Scripts/pip.exe" install -r requirements.txt -q

echo ""
echo "=========================================="
echo "  🎙️  PDFTwocast 启动中..."
echo "  访问地址: http://localhost:7860"
echo "=========================================="
echo ""

"$VENV/Scripts/python.exe" main.py

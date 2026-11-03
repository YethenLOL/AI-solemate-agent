#!/usr/bin/env bash
# 建虚拟环境、装依赖、准备 .env。macOS / Linux 用。
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env：请填 LLM_API_KEY、OWNER_QQ、ONEBOT_ACCESS_TOKEN"
else
  echo ".env 已存在，没有覆盖"
fi

echo
echo "装好了。下一步："
echo "  1. 编辑 .env"
echo "  2. .venv/bin/python -m dafeiyu --check"
echo "  3. .venv/bin/python -m dafeiyu --test-qq"
echo "  4. .venv/bin/python -m dafeiyu"

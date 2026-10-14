#!/bin/sh
set -eu
umask 077
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TASK_ROOT"
TASK_PYTHON=${AUTOEXAM_PYTHON:-python3}
"$TASK_PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if [ ! -x .venv/bin/python ]; then "$TASK_PYTHON" -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps -e .
PLAYWRIGHT_BROWSERS_PATH="$TASK_ROOT/.browser" .venv/bin/python -m playwright install chromium
if [ ! -f config.yaml ]; then cp config.example.yaml config.yaml; fi
if [ ! -f .env ]; then cp .env.example .env; fi
chmod 600 .env config.yaml
chmod +x ./*.sh scripts/launch.sh
.venv/bin/python -m pytest -q
.venv/bin/python -m autoexam demo
.venv/bin/python -m autoexam plist
if [ "${1:-}" = "--schedule" ]; then
  TASK_AGENT="$HOME/Library/LaunchAgents/com.local.exampilot.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cp launchd/com.local.exampilot.plist "$TASK_AGENT"
  launchctl bootout "gui/$(id -u)" "$TASK_AGENT" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$TASK_AGENT"
  printf '%s\n' '已安装定时任务；请核对 status、登录会话和 API Key。'
else
  printf '%s\n' '安装检查完成。配置并验证后运行 ./install.sh --schedule 安装定时任务。'
fi


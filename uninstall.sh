#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TASK_ROOT"
./stop.sh
TASK_AGENT="$HOME/Library/LaunchAgents/com.local.exampilot.plist"
launchctl bootout "gui/$(id -u)" "$TASK_AGENT" 2>/dev/null || true
rm -f "$TASK_AGENT"
printf '%s\n' '定时任务已卸载；源码、状态、日志及本机凭据保留。删除凭据请手动删除 runtime/session.json 和 .env。'


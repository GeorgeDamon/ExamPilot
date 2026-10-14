#!/bin/sh
set -eu
umask 077
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_ROOT"
# Keep an already-awake Mac awake during the monitoring process. Does not wake it.
exec /usr/bin/caffeinate -i "$TASK_ROOT/.venv/bin/python" -m autoexam scheduled


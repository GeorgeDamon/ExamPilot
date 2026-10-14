#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TASK_ROOT"
exec "$TASK_ROOT/.venv/bin/python" -m autoexam stop


#!/usr/bin/env bash
# Move to script directory
cd "$(dirname "$0")"

# Activate virtualenv and run
./.venv/bin/python main.py --max-apps 10 >> run_daily.log 2>&1

#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/damai_appium/environment.sh"
check_driver
exec appium --address 127.0.0.1 --port 4723

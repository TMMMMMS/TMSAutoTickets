#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/damai_appium/environment.sh"
project_python -m damai_appium.damai_app_v2 --check "$@"

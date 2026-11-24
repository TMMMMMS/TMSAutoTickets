project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
cd "$project_root"
project_python() {
    if [[ -x .venv/bin/python ]]; then .venv/bin/python "$@"
    elif command -v poetry >/dev/null; then poetry run python "$@"
    else python3 "$@"; fi
}
check_driver() {
    if ! node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(+!(a>=24 || a===22 && b>=12 || a===20 && b>=19))' 2>/dev/null; then
        for runtime in "$HOME"/.nvm/versions/node/v24.*/bin; do
            if [[ -x "$runtime/node" ]]; then export PATH="$runtime:$PATH"; break; fi
        done
    fi
    node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(+!(a>=24 || a===22 && b>=12 || a===20 && b>=19))' || { echo "Appium 3 需要兼容的 Node，请参阅快速开始" >&2; return 1; }
    tools_dir="$HOME/.local/share/damai-tools"
    if ! command -v appium >/dev/null && [[ -x "$tools_dir/server/node_modules/.bin/appium" ]]; then
        export PATH="$tools_dir/server/node_modules/.bin:$PATH"
        export APPIUM_HOME="${APPIUM_HOME:-$tools_dir/drivers}"
    fi
    appium driver list --installed --json | project_python -c 'import json,sys; sys.exit(0 if "uiautomator2" in json.load(sys.stdin) else "缺少 UiAutomator2 驱动")'
}

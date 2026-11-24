import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from damai_appium import damai_app_v2 as cli
from damai_appium import device


@pytest.fixture
def cli_run(monkeypatch):
    config = SimpleNamespace(if_commit_order=True, server_url="http://appium.example:4723")
    monkeypatch.setattr(cli.Config, "load_config", lambda path: config)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    return config


def test_piped_yes_cannot_start_live_purchase(cli_run, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["purchase"])
    connect = Mock()
    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "DamaiBot", Mock())
    assert cli.main() != 0
    connect.assert_not_called()
    assert "交互终端" in capsys.readouterr().err


@pytest.mark.parametrize("answer,starts", [("y", True), ("N", False), ("", False)])
def test_interactive_live_confirmation(cli_run, monkeypatch, answer, starts):
    monkeypatch.setattr(sys, "argv", ["purchase"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    connect = Mock()
    monkeypatch.setattr(cli, "connect", connect)
    factory = Mock()
    factory.return_value.run.return_value = "订单已生成"
    monkeypatch.setattr(cli, "DamaiBot", factory)
    assert cli.main() == 0
    assert connect.called == starts


def test_piped_dry_run_never_enables_submit(cli_run, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["purchase", "--dry-run"])
    monkeypatch.setattr(cli, "connect", Mock())
    factory = Mock()
    factory.return_value.run.return_value = "演练完成"
    monkeypatch.setattr(cli, "DamaiBot", factory)
    assert cli.main() == 0
    assert factory.call_args.args[0].if_commit_order is False


@pytest.mark.parametrize("mode", [["--check"], ["--dry-run"]])
@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["adb", "devices"], 10),
                                   subprocess.CalledProcessError(3, ["adb", "devices"])])
def test_adb_errors_at_cli(cli_run, monkeypatch, capsys, mode, error):
    monkeypatch.setattr(sys, "argv", ["purchase", *mode])
    monkeypatch.setenv("ANDROID_HOME", "/test-sdk")
    monkeypatch.setattr(device, "urlopen", lambda *a, **kw: io.BytesIO(b'{"value":{"ready":true}}'))
    monkeypatch.setattr(device.subprocess, "check_output", Mock(side_effect=error))
    assert cli.main() == 1
    message = capsys.readouterr().err
    assert "adb devices" in message
    assert ("超时" if isinstance(error, subprocess.TimeoutExpired) else "退出码 3") in message


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher on POSIX")
@pytest.mark.parametrize("script,args", [("check_environment.sh", []),
                                         ("start_ticket_grabbing.sh", ["--dry-run"])])
def test_client_scripts_without_server_tools(tmp_path, script, args):
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "damai_appium"
    scripts.mkdir()
    shutil.copy(root / "damai_appium/environment.sh", scripts)
    shutil.copy(root / script, tmp_path)
    bin_dir = tmp_path / ".venv/bin"
    bin_dir.mkdir(parents=True)
    interpreter = bin_dir / "python"
    interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    interpreter.chmod(0o755)
    path = tmp_path / "bin"
    path.mkdir()
    for name in ("bash", "dirname"):
        (path / name).symlink_to(shutil.which(name))
    result = subprocess.run([str(tmp_path / script), *args], input="", text=True, capture_output=True,
                            env={**os.environ, "PATH": str(path), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert "damai_appium.damai_app_v2" in result.stdout.splitlines()
    assert ("--check" if not args else "--dry-run") in result.stdout.splitlines()


@pytest.mark.skipif(os.name != "posix", reason="Bash launcher on POSIX")
def test_appium_matches_selected_runtime_and_driver_home(tmp_path):
    root = Path(__file__).resolve().parents[2]
    old_bin = tmp_path / "bin"
    node24 = tmp_path / ".nvm/versions/node/v24.12.0/bin"
    bundled = tmp_path / ".local/share/damai-tools/server/node_modules/.bin"
    for directory in (old_bin, node24, bundled):
        directory.mkdir(parents=True)
    for name in ("bash", "dirname"):
        (old_bin / name).symlink_to(shutil.which(name))
    for command, body in [(old_bin / "node", "exit 1"), (node24 / "node", "exit 0"),
                          (bundled / "appium", "exit 42"),
                          (node24 / "appium", 'if [ "$1" = driver ]; then echo \'{"uiautomator2":{}}\'; else echo "nvm:${APPIUM_HOME-unset}"; fi')]:
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)
    (old_bin / "python3").symlink_to(sys.executable)
    env = {**os.environ, "PATH": str(old_bin), "HOME": str(tmp_path)}
    env.pop("APPIUM_HOME", None)
    result = subprocess.run([str(root / "start_appium.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "nvm:unset"

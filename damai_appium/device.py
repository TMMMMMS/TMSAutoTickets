import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

from appium import webdriver
from appium.options.android import UiAutomator2Options


def select_device(listing, serial=None):
    devices = dict(row.split()[:2] for row in listing.splitlines()[1:] if len(row.split()) >= 2)
    if serial:
        if devices.get(serial) != "device":
            raise ValueError("指定设备离线或尚未授权 USB 调试")
        return serial
    if len(devices) != 1 or "device" not in devices.values():
        raise ValueError("请连接一台已授权设备，多设备请设置 ANDROID_SERIAL")
    return next(iter(devices))


def device_info(server_url):
    with urlopen(server_url + "/status", timeout=4) as response:
        if json.load(response)["value"].get("ready") is not True:
            raise ValueError("Appium 服务尚未就绪")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    adb = str(Path(sdk) / "platform-tools/adb") if sdk else shutil.which("adb")
    if not adb:
        raise ValueError("找不到 Android SDK 的 adb")

    def query(*args):
        try:
            return subprocess.check_output([adb, *args], text=True, timeout=10,
                                           stderr=subprocess.PIPE).strip()
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"adb {' '.join(args)} 超时，请检查设备连接") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise RuntimeError(f"adb {' '.join(args)} 失败（退出码 {exc.returncode}） {detail}".rstrip()) from exc

    serial = select_device(query("devices"), os.environ.get("ANDROID_SERIAL"))
    if not query("-s", serial, "shell", "pm", "path", "cn.damai").startswith("package:"):
        raise ValueError("目标设备未安装大麦")
    return serial, query("-s", serial, "shell", "getprop", "ro.build.version.release")


def connect(server_url):
    serial, version = device_info(server_url)
    options = UiAutomator2Options().load_capabilities({
        "udid": serial, "platformVersion": version, "noReset": True,
        "autoLaunch": False, "shouldTerminateApp": False,
        "appPackage": "cn.damai", "appActivity": ".launcher.splash.SplashMainActivity",
    })
    return webdriver.Remote(server_url, options=options)

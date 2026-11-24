import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from .page import PurchaseStopped


BEIJING = timezone(timedelta(hours=8))


class WindowExpired(PurchaseStopped):
    pass


class NoStock(PurchaseStopped):
    pass


class SaleWindow:
    def __init__(self, sale_at, minutes):
        self.start = sale_at.timestamp()
        self.end = self.start + minutes * 60
        self.deadline = None

    def remaining(self):
        remaining = self.end - time.time()
        if self.deadline is not None:
            remaining = min(remaining, self.deadline - time.monotonic())
        if remaining <= 0:
            raise WindowExpired("余票检查时间已到，已停止购买")
        return remaining

    def wait_for_open(self, keep_alive):
        while True:
            remaining = self.remaining()
            delay = self.start - time.time()
            if delay <= 0:
                self.deadline = time.monotonic() + remaining
                return
            time.sleep(min(delay, 20))
            if self.start - time.time() > 3:
                keep_alive()

    def pause(self, seconds):
        time.sleep(min(seconds, self.remaining()))
        self.remaining()

    def log_time(self, action):
        now = time.time()
        stamp = datetime.fromtimestamp(now, BEIJING).isoformat(timespec="milliseconds")
        print(f"{action} {stamp}，相对开售 {now - self.start:+.3f} 秒", flush=True)


def validate_sale_label(texts, sale_at):
    for text in texts:
        stamp = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})", text)
        if not stamp:
            raise PurchaseStopped(f"无法核对页面开售时间：{text}")
        year, month, day, hour, minute = stamp.groups()
        values = (int(year or sale_at.year), int(month), int(day), int(hour), int(minute))
        if values != (sale_at.year, sale_at.month, sale_at.day, sale_at.hour, sale_at.minute):
            raise PurchaseStopped(f"配置的开售时间与页面不符：{text}")


def notify(title, message):
    script = ('on run argv\n'
              'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\n'
              'end run')
    try:
        subprocess.run(["osascript", "-", title, message], input=script, text=True,
                       capture_output=True, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Mac 通知发送失败，请以终端结果为准：{exc}", file=sys.stderr)
        return False
    return True

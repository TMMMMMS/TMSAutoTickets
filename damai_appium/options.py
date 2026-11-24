import json
import platform
import re
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from selenium.webdriver.support.ui import WebDriverWait

from .page import Page, PurchaseStopped, labels, session_label_complete


_vision_command = ContextVar("vision_command", default=None)


@contextmanager
def prepare_vision():
    if platform.system() != "Darwin":
        yield
        return
    with tempfile.TemporaryDirectory(prefix="damai-vision-") as directory:
        binary = str(Path(directory) / "read-options")
        source = str(Path(__file__).with_name("read_options.swift"))
        try:
            subprocess.run(["swiftc", source, "-o", binary], check=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PurchaseStopped("Vision 准备失败，请检查 swiftc 命令") from exc
        token = _vision_command.set([binary])
        try:
            yield
        finally:
            _vision_command.reset(token)


def option_texts(page, container, session=None):
    previous = None
    def settled(_):
        nonlocal previous
        current = Page(page.driver)
        groups = current.ids(container)
        if len(groups) != 1:
            previous = None
            return False
        bounds = [row.get("bounds") for row in groups[0]]
        same = bool(bounds) and bounds == previous
        previous = bounds
        return groups[0] if same else False
    rows = list(WebDriverWait(page.driver, 6).until(settled))
    if not rows:
        raise PurchaseStopped("选项尚未显示")
    return list(zip(rows, read_texts(page, rows, session)))


def read_texts(page, rows, session=None):
    def complete(value):
        if session is None:
            return bool(value)
        return session_label_complete(value, *session)

    values = [" ".join(labels(row)) for row in rows]
    missing = [i for i, value in enumerate(values) if not complete(value)]
    if missing:
        if platform.system() != "Darwin":
            raise PurchaseStopped("当前选项没有可读文字，请人工核对")
        regions = []
        for i in missing:
            edges = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", rows[i].get("bounds", ""))
            if not edges:
                raise PurchaseStopped("无法确定选项的可见区域")
            regions.append(",".join(edges.groups()))
        reader = _vision_command.get() or ["swift", str(Path(__file__).with_name("read_options.swift"))]
        command = [*reader, *regions]
        screenshot = page.driver.get_screenshot_as_png()
        try:
            result = subprocess.run(command, input=screenshot, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=45)
        except subprocess.TimeoutExpired as exc:
            raise PurchaseStopped("选项识别超时，请人工核对") from exc
        if result.returncode:
            raise PurchaseStopped("macOS Vision 未能读取选项，请检查 swift 命令")
        for i, value in zip(missing, json.loads(result.stdout)):
            values[i] = (values[i] + " " + value).strip()
    if any(not complete(values[i]) for i in missing):
        raise PurchaseStopped("有选项文字无法识别，请人工确认")
    return values

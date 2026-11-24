import re
import xml.etree.ElementTree as ET

from selenium.webdriver.common.by import By


class PurchaseStopped(RuntimeError):
    pass


def label(node):
    return (node.get("text") or node.get("content-desc") or "").strip()


def labels(node):
    return [label(item) for item in node.iter() if label(item)]


def unique(items, description):
    if len(items) != 1:
        raise PurchaseStopped(f"{description}匹配到 {len(items)} 项，请在页面核对")
    return items[0]


DATE = re.compile(r"(?<!\d)(?:(\d{4})[年./-])?(\d{1,2})[月./-](\d{1,2})(?:日)?(?!\d)")
CLOCK = r"([01]?[0-9]|2[0-3])[:：]([0-5][0-9])"
CLOCK_IN_TEXT = re.compile(r"(?<!\d)(?<!\d:)(?<!\d：)" + CLOCK + r"(?!\d)")


def requested_date(target):
    wanted = DATE.fullmatch(target)
    if not wanted:
        raise PurchaseStopped("日期请使用 YYYY-MM-DD 或 MM.DD")
    return wanted


def session_times(value):
    return {(int(hour), minute) for hour, minute in CLOCK_IN_TEXT.findall(value)}


def session_label_complete(value, date, session_time):
    dates = DATE.findall(value)
    days = {(int(month), int(day)) for _, month, day in dates}
    years = {year for year, _, _ in dates if year}
    times = session_times(value)
    if len(days) > 1 or len(years) > 1 or (session_time and len(times) > 1):
        raise PurchaseStopped("场次信息有冲突，请在页面核对")
    has_date = bool(days) and (not requested_date(date)[1] or bool(years))
    return has_date and (not session_time or bool(times))


def matches_date(value, target):
    wanted = requested_date(target)
    for actual in DATE.finditer(value):
        if wanted[1] and actual[1] != wanted[1]:
            continue
        if int(actual[2]) == int(wanted[2]) and int(actual[3]) == int(wanted[3]):
            return True
    return False


def matches_session(value, date, session_time):
    if not matches_date(value, date):
        return False
    if not session_time:
        return True
    wanted = re.fullmatch(CLOCK, session_time)
    if not wanted:
        raise PurchaseStopped("场次时间请使用 H:MM 或 HH:MM")
    return session_times(value) == {(int(wanted[1]), wanted[2])}


def literal(value):
    if "'" not in value:
        return "'" + value + "'"
    return "concat(" + ", \"'\", ".join("'" + part + "'" for part in value.split("'")) + ")"


class Page:
    def __init__(self, driver):
        self.driver = driver
        if driver.current_package != "cn.damai":
            raise PurchaseStopped("请在真机前台打开大麦的目标演出详情页")
        self.root = ET.fromstring(driver.page_source)
        self.parents = {child: parent for parent in self.root.iter() for child in parent}
        self.nodes = [n for n in self.root.iter() if n.get("package") == "cn.damai"
                      and n.get("displayed") != "false"]

    def ids(self, *names):
        ids = {"cn.damai:id/" + name for name in names}
        return [node for node in self.nodes if node.get("resource-id") in ids]

    def named(self, *names):
        return [node for node in self.nodes if label(node) in names]

    def texts(self, *ids):
        return [value for node in self.ids(*ids) for value in labels(node)]

    def click(self, node, before_click=None):
        if self.driver.current_package != "cn.damai":
            raise PurchaseStopped("前台应用已切换")
        fields = ("package", "resource-id", "class", "text", "content-desc", "bounds")
        terms = ["@" + field + "=" + literal(node.get(field)) for field in fields if node.get(field)]
        elements = self.driver.find_elements(By.XPATH, "//*[" + " and ".join(terms) + "]")
        element = unique(elements, "当前页面控件")
        if not element.is_displayed() or not element.is_enabled():
            raise PurchaseStopped("目标控件当前不可操作")
        if before_click is not None:
            before_click()
        element.click()

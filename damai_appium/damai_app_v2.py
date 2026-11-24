import argparse
from contextlib import nullcontext
import re
import sys
import time
from xml.etree.ElementTree import ParseError
from urllib3.exceptions import HTTPError
from decimal import Decimal

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from .config import Config
    from .device import connect, device_info
    from .options import option_texts, prepare_vision, read_texts
    from .schedule import NoStock, SaleWindow, WindowExpired, notify, validate_sale_label
    from .page import Page, PurchaseStopped, label, matches_session, unique
else:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from damai_appium.config import Config
    from damai_appium.device import connect, device_info
    from damai_appium.options import option_texts, prepare_vision, read_texts
    from damai_appium.schedule import NoStock, SaleWindow, WindowExpired, notify, validate_sale_label
    from damai_appium.page import Page, PurchaseStopped, label, matches_session, unique


VERIFICATION_ACTIVITY = "com.alibaba.wireless.security.open.middletier.fc.ui.ContainerActivity"
PAGE_TIMEOUT = 8
POLL_INTERVAL = 0.35


def check_stock(page):
    if page.named("库存不足", "余票不足", "所选票档已售罄"):
        raise NoStock("本次目标余票不足")


def compact(value):
    return "".join(value.split())


def title_key(value):
    return re.sub(r"[【】·\s]", "", value)


def price_key(value):
    return re.sub("元$", "", compact(value).replace("￥", "").replace("¥", ""))


def amount(value):
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        raise PurchaseStopped("无法读取票价金额")
    return Decimal(match[0])


def selected_count(page, unit_price):
    quantities = page.texts("tv_num")
    if quantities:
        if len(quantities) != 1:
            return None
        match = re.fullmatch(r"([1-9][0-9]*)\s*张?", quantities[0])
        if not match:
            return None
        count = int(match[1])
    elif not page.ids("tv_num", "img_jia", "img_jian"):
        count = 1
    else:
        return None
    prices = page.texts("tv_price")
    if len(prices) == 1 and re.fullmatch(r"\d+(?:\.\d+)?", prices[0]):
        if Decimal(prices[0]) == unit_price * count:
            return count
    return None


def sale_state(value):
    if compact(value) in {"立即购买", "立即购票", "立即预订", "选座购买"}:
        return "buy"
    if any(word in value for word in ("缺货", "售罄", "暂时无票")):
        return "empty"
    if any(word in value for word in ("待开售", "后开抢", "即将开售", "预约", "开售提醒", "后开售")):
        return "wait"
    raise PurchaseStopped(f"当前销售状态：{value}")


def pick(options, predicate, description):
    matches = [(row, value) for row, value in options if predicate(value)]
    row, value = unique(matches, description)
    if row.get("enabled") == "false" or any(word in value for word in ("售罄", "缺货", "无票")):
        raise NoStock(f"{description}不可购买")
    return row


def viewer_states(page):
    result = {}
    for name_node in page.ids("text_name", "tv_viewer_name"):
        row = page.parents.get(name_node)
        while row is not None:
            names = [n for n in row.iter() if n in page.ids("text_name", "tv_viewer_name")]
            if len(names) > 1:
                break
            boxes = [n for n in row.iter() if n.get("checkable") == "true"]
            if len(boxes) == 1:
                name = label(name_node)
                if name in result:
                    raise PurchaseStopped("出现同名观演人")
                result[name] = boxes[0]
                break
            row = page.parents.get(row)
        if label(name_node) not in result:
            raise PurchaseStopped("无法确认观演人勾选状态")
    return result


class DamaiBot:
    def __init__(self, config, driver):
        self.config = config
        self.driver = driver
        self.sent = False
        self.window = SaleWindow(config.sale_at, config.retry_minutes) if config.sale_at else None
        self.first_click = True

    def check_deadline(self):
        if self.window:
            self.window.remaining()

    def click(self, page, node):
        if self.window:
            page.click(node, before_click=self.check_deadline)
        else:
            page.click(node)

    def detail(self, page):
        title = "".join(page.texts("info_v2_title_tv1", "info_v2_title_tv2"))
        if title_key(title) != title_key(self.config.keyword):
            raise PurchaseStopped("演出全名与当前详情页不符")
        venues = page.texts("venue_name_0", "tv_project_city")
        if not any(re.split(r"[市·|｜\s]", place)[0] == self.config.city.rstrip("市") for place in venues):
            raise PurchaseStopped("当前演出城市不符")
        if self.window:
            validate_sale_label(page.texts("id_project_count_sell_time"), self.config.sale_at)
        return unique(page.ids("trade_project_detail_purchase_status_bar_container_fl"), "购票入口")

    def active_page(self, on_verification=None):
        while True:
            self.check_deadline()
            page = Page(self.driver)
            if not self.window:
                return page
            texts = [label(n) for n in page.nodes]
            if "登录大麦" in texts:
                raise PurchaseStopped("页面要求人工验证或登录，请接手")
            verification = any(t in ("请完成验证", "安全验证", "滑动验证") for t in texts)
            if verification or (on_verification and self.driver.current_activity == VERIFICATION_ACTIVITY):
                if on_verification is None:
                    raise PurchaseStopped("页面要求人工验证或登录，请接手")
                on_verification()
                self.window.pause(POLL_INTERVAL)
                continue
            if not any(t.startswith(("正在排队", "排队中", "您正在排队")) for t in texts):
                return page
            self.window.pause(1)

    def until(self, predicate, description):
        def ready(_):
            page = self.active_page()
            if self.window:
                check_stock(page)
            if predicate(page):
                return page
            return False
        try:
            return WebDriverWait(self.driver, PAGE_TIMEOUT, poll_frequency=POLL_INTERVAL).until(ready)
        except TimeoutException as exc:
            raise PurchaseStopped(f"未确认{description}") from exc

    def open_sale(self):
        last_refresh = time.monotonic()
        while True:
            page = self.active_page()
            button = self.detail(page)
            state = sale_state(read_texts(page, [button])[0])
            if state == "buy":
                if self.window and self.first_click:
                    self.window.log_time("首次识别可购")
                    def mark_click():
                        self.check_deadline()
                        self.window.log_time("首次发起购买点击")
                    page.click(button, before_click=mark_click)
                    self.first_click = False
                else:
                    self.click(page, button)
                return
            if not self.window:
                if state == "empty":
                    raise NoStock("目标演出暂无库存")
                time.sleep(1)
                continue
            if time.monotonic() - last_refresh >= 5:
                self.refresh_detail()
                last_refresh = time.monotonic()
            self.window.pause(1)

    def refresh_detail(self):
        page = self.active_page()
        self.detail(page)
        size = self.driver.get_window_size()
        x = size["width"] // 2
        self.check_deadline()
        self.driver.swipe(x, size["height"] // 3, x, size["height"] * 3 // 4, 450)

    def return_to_detail(self):
        for _ in range(3):
            page = self.active_page()
            if page.ids("trade_project_detail_purchase_status_bar_container_fl"):
                self.detail(page)
                return
            if page.ids("project_detail_perform_price_flowlayout"):
                if title_key(self.config.keyword) not in {title_key(v) for v in page.texts("tv_title")}:
                    raise PurchaseStopped("恢复选票时演出不符")
            elif "确认购买" in page.texts("order_activity_title"):
                if not any(title_key(label(n)) == title_key(self.config.keyword) for n in page.nodes):
                    raise PurchaseStopped("恢复确认页时演出不符")
            else:
                raise PurchaseStopped("未识别库存更新入口，请在大麦核对页面")
            self.check_deadline()
            self.driver.back()
            self.window.pause(0.5)
        raise PurchaseStopped("无法返回目标详情页")

    def wait_for_session_list(self, date_id):
        if not self.window:
            return self.until(lambda page: page.ids(date_id), "场次列表")

        load_deadline = time.monotonic() + PAGE_TIMEOUT
        prompted = False

        def on_verification():
            nonlocal load_deadline, prompted
            load_deadline = None
            if not prompted:
                print("请在手机上完成验证，正在等待目标场次列表。按 Ctrl+C 可取消。", flush=True)
                prompted = True

        while True:
            page = self.active_page(on_verification=on_verification)
            self.check_deadline()
            check_stock(page)
            if page.ids(date_id):
                return page
            now = time.monotonic()
            if load_deadline is None:
                load_deadline = now + PAGE_TIMEOUT
            remaining = load_deadline - now
            if remaining <= 0:
                raise PurchaseStopped("未确认场次列表")
            self.window.pause(min(POLL_INTERVAL, remaining))

    def select_tickets(self):
        cfg = self.config
        date_id = "project_detail_perform_flowlayout"
        price_id = "project_detail_perform_price_flowlayout"
        page = self.wait_for_session_list(date_id)
        if title_key(cfg.keyword) not in {title_key(text) for text in page.texts("tv_title")}:
            raise PurchaseStopped("选票页演出不符")
        row = pick(option_texts(page, date_id, (cfg.date, cfg.session_time)),
                   lambda text: matches_session(text, cfg.date, cfg.session_time), "目标场次")
        self.click(page, row)
        page = self.until(lambda p: p.ids(price_id), "票档列表")
        def target_tier(value):
            clean = re.sub(r"售罄|缺货登记|缺货|无票", "", value)
            return price_key(clean) == price_key(cfg.price)
        self.click(page, pick(option_texts(page, price_id), target_tier, "目标票档"))
        price = amount(cfg.price)
        page = self.until(lambda p: selected_count(p, price) is not None, "当前张数及票价")
        count = selected_count(page, price)
        target = len(cfg.users)
        step = 1 if count < target else -1
        for expected in range(count + step, target + step, step):
            control = "img_jia" if step > 0 else "img_jian"
            self.click(page, unique(page.ids(control), "调整张数按钮"))
            page = self.until(lambda p: selected_count(p, price) == expected, "调整后的张数及票价")
        self.click(page, unique(page.ids("btn_buy_view"), "选票确定按钮"))

    def confirm(self):
        cfg = self.config
        page = self.until(lambda p: "确认购买" in p.texts("order_activity_title"), "订单确认页")
        for name in cfg.users:
            boxes = viewer_states(page)
            if name not in boxes:
                raise PurchaseStopped("未找到配置中的观演人")
            if boxes[name].get("checked") != "true":
                self.click(page, boxes[name])
                page = self.until(lambda p: name in viewer_states(p) and
                                  viewer_states(p)[name].get("checked") == "true", "观演人勾选")
        checked = {name for name, box in viewer_states(page).items() if box.get("checked") == "true"}
        if checked != set(cfg.users):
            raise PurchaseStopped("确认页勾选的观演人与配置不一致")
        values = [label(node) for node in page.nodes]
        if title_key(cfg.keyword) not in {title_key(v) for v in values}:
            raise PurchaseStopped("确认页演出不符")
        sessions = [v for v in values if matches_session(v, cfg.date, cfg.session_time)]
        unique(sessions, "确认页场次")
        amounts = [amount(v) for v in values if re.fullmatch(r"[￥¥]\s*\d+(?:\.\d+)?\s*票档", v)]
        if amounts != [amount(cfg.price)] or f"×{len(cfg.users)}张" not in {compact(v) for v in values}:
            raise PurchaseStopped("确认页票价或张数不符")
        totals = [v for v in values if re.fullmatch(r"[￥¥]\s*\d+(?:\.\d+)?", v)]
        print(f"已核对 {cfg.date} {cfg.session_time}，{cfg.price}，{len(cfg.users)} 张，"
              f"观演人 {', '.join(cfg.users)}，页面金额 {' / '.join(totals) or '未显示'}")
        return page

    def finish(self, page):
        self.check_deadline()
        if not self.config.if_commit_order:
            return "演练完成，已停在确认页，提交次数为零"
        if self.sent:
            raise PurchaseStopped("本次已尝试提交，请到大麦订单核实")
        button = unique(page.named("立即提交", "提交订单"), "提交按钮")
        def start_submit():
            self.check_deadline()
            self.sent = True
        try:
            if self.window:
                page.click(button, before_click=start_submit)
            else:
                self.sent = True
                page.click(button)
            def outcome(_):
                current = Page(self.driver)
                texts = [label(n) for n in current.nodes]
                evidence = any(re.fullmatch(r"订单(?:编号|号)[：:]\s*\d+", t) for t in texts)
                correct_show = any(title_key(t) == title_key(self.config.keyword) for t in texts)
                correct_time = any(matches_session(t, self.config.date, self.config.session_time) for t in texts)
                if evidence and correct_show and correct_time and "确认购买" not in current.texts("order_activity_title"):
                    return "订单已生成，请在大麦核对并付款"
                if current.named("订单创建失败", "提交失败"):
                    if current.named("库存不足", "已售罄") and not evidence and correct_show and correct_time:
                        return "库存不足，订单创建失败"
                    return "订单创建失败"
                if not self.window and current.named("库存不足", "已售罄"):
                    return "订单创建失败"
                return False
            result = WebDriverWait(self.driver, 12).until(outcome)
            if self.window and result == "库存不足，订单创建失败":
                self.sent = False
                raise NoStock(result)
            return result
        except (WindowExpired, NoStock):
            raise
        except (WebDriverException, HTTPError, ParseError, PurchaseStopped, OSError, KeyboardInterrupt):
            if not self.sent:
                raise
            return "提交结果待核实，请查看大麦订单"

    def run(self):
        if not self.window:
            self.open_sale()
            self.select_tickets()
            return self.finish(self.confirm())
        self.window.remaining()
        def preflight():
            self.detail(Page(self.driver))
        preflight()
        cfg = self.config
        print(f"已就绪：{cfg.keyword}，{cfg.date} {cfg.session_time}，{cfg.price}，"
              f"{len(cfg.users)} 张。北京时间 {cfg.sale_at:%Y-%m-%d %H:%M:%S} 开售，"
              f"余票窗口 {cfg.retry_minutes} 分钟", flush=True)
        self.window.wait_for_open(preflight)
        self.window.log_time("首轮检查")
        while True:
            self.check_deadline()
            try:
                self.open_sale()
                self.select_tickets()
                return self.finish(self.confirm())
            except NoStock as exc:
                if self.sent:
                    return "提交结果待核实，请查看大麦订单"
                print(f"{exc}，3 秒后检查目标余票", flush=True)
                self.window.pause(3)
                self.return_to_detail()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--test-notification", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    driver = None
    scheduled = False
    try:
        if args.test_notification:
            ok = notify("大麦通知测试", "这是测试通知，未发起购买。")
            print("已请求 macOS 显示测试通知" if ok else "测试通知发送失败")
            return 0 if ok else 1
        cfg = Config.load_config(args.config)
        if args.check:
            _, version = device_info(cfg.server_url)
            print(f"Appium 已就绪，Android {version}，已安装大麦")
            return 0
        if args.dry_run:
            cfg.if_commit_order = False
        scheduled = bool(getattr(cfg, "sale_at", None))
        if scheduled:
            SaleWindow(cfg.sale_at, cfg.retry_minutes).remaining()
            print(f"{cfg.keyword}，{cfg.date} {cfg.session_time}，{cfg.price}，"
                  f"{len(cfg.users)} 张，观演人 {', '.join(cfg.users)}，"
                  f"开售 {cfg.sale_at:%Y-%m-%d %H:%M:%S} 北京时间", flush=True)
        if cfg.if_commit_order:
            if not sys.stdin.isatty():
                print("正式购票需要在交互终端确认，演练可使用 --dry-run", file=sys.stderr)
                return 1
            try:
                answer = input("已开启自动提交订单。确认开始正式购票？[y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() != "y":
                print("已取消购票")
                return 0
        with prepare_vision() if scheduled else nullcontext():
            driver = connect(cfg.server_url)
            driver.update_settings({"waitForIdleTimeout": 150})
            result = DamaiBot(cfg, driver).run()
        print(result, flush=True)
        if scheduled:
            notify(result, f"{cfg.keyword}  {cfg.date} {cfg.session_time}")
        return 0 if result.startswith(("演练完成", "订单已生成")) else 1
    except KeyboardInterrupt:
        print("用户已取消等待")
        return 1
    except (ValueError, OSError, RuntimeError, WebDriverException, HTTPError, ParseError) as exc:
        print(str(exc), file=sys.stderr)
        if scheduled:
            notify("大麦购票已停止", f"{cfg.keyword}  {cfg.date} {cfg.session_time}：{exc}")
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except (WebDriverException, HTTPError, OSError):
                print("驱动会话清理失败", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

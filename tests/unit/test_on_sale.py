import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree.ElementTree import Element

import pytest
from selenium.common.exceptions import WebDriverException

from damai_appium import damai_app_v2 as flow
from damai_appium import schedule
from damai_appium.config import Config


EXAMPLE = Path(__file__).resolve().parents[2] / "damai_appium/xian.example.json"


@pytest.fixture
def cfg():
    return Config.load_config(EXAMPLE)


@pytest.fixture
def clock(cfg, monkeypatch):
    state = SimpleNamespace(wall=cfg.sale_at.timestamp() - 41, monotonic=1000.0)
    def advance(seconds):
        state.wall += seconds
        state.monotonic += seconds
    state.advance = advance
    monkeypatch.setattr(schedule, "time", SimpleNamespace(time=lambda: state.wall,
                        monotonic=lambda: state.monotonic, sleep=advance))
    return state


@pytest.mark.parametrize("changes", [{"ticket_count": 2}, {"ticket_count": True}, {"retry_minutes": 11},
                                     {"retry_minutes": 4}, {"retry_minutes": 6.5}, {"sale_at": "10:08"}])
def test_invalid_schedule(tmp_path, changes):
    data = json.loads(EXAMPLE.read_text())
    data.update(changes)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Config.load_config(p)


def test_beijing_label(cfg):
    assert cfg.sale_at.isoformat() == "2026-11-24T10:08:00+08:00"
    schedule.validate_sale_label(["11月24日 10:08开抢"], cfg.sale_at)
    with pytest.raises(flow.PurchaseStopped, match="不符"):
        schedule.validate_sale_label(["11月24日 10:18开抢"], cfg.sale_at)


@pytest.mark.parametrize("minutes", [5, 10])
def test_clock_boundary_and_keepalive(cfg, clock, minutes):
    timer = schedule.SaleWindow(cfg.sale_at, minutes)
    heartbeat = Mock()
    timer.wait_for_open(heartbeat)
    assert heartbeat.called and clock.wall == timer.start
    clock.advance(minutes * 60 - 1)
    assert timer.remaining() == 1
    clock.wall -= 15
    with pytest.raises(schedule.WindowExpired):
        timer.pause(3)


def test_late_start_and_wake(cfg, clock):
    timer = schedule.SaleWindow(cfg.sale_at, 5)
    clock.wall = timer.end - 2
    timer.wait_for_open(Mock())
    assert timer.remaining() == 2
    clock.wall += 60
    with pytest.raises(schedule.WindowExpired):
        timer.remaining()


def test_expired_before_start(cfg, clock):
    timer = schedule.SaleWindow(cfg.sale_at, 5)
    clock.wall = timer.end
    with pytest.raises(schedule.WindowExpired):
        timer.wait_for_open(Mock())


def test_cancel_wait(cfg, clock, monkeypatch):
    timer = schedule.SaleWindow(cfg.sale_at, 5)
    monkeypatch.setattr(schedule.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        timer.wait_for_open(Mock())


def test_restock_rehearsal(cfg, clock, monkeypatch):
    bot = flow.DamaiBot(cfg, Mock())
    page = Mock()
    monkeypatch.setattr(flow, "Page", lambda _: page)
    bot.detail = Mock()
    receipts = []
    bot.open_sale = lambda: receipts.append(clock.wall)
    bot.select_tickets = Mock(side_effect=[schedule.NoStock("缺货"), None])
    bot.return_to_detail = Mock()
    bot.confirm = Mock(return_value=page)
    assert "零" in bot.run()
    assert receipts == [bot.window.start, bot.window.start + 3]
    bot.return_to_detail.assert_called_once()
    page.click.assert_not_called()


@pytest.mark.parametrize("purchase_label", ["立即购票", "立即预订"])
def test_first_click_only_after_page_buyable(cfg, clock, monkeypatch, purchase_label):
    class Page:
        nodes = []
        def click(self, button, before_click):
            before_click()
            assert clock.wall >= cfg.sale_at.timestamp()
    page = Page()
    bot = flow.DamaiBot(cfg, Mock())
    bot.window.wait_for_open(lambda: None)
    bot.detail = Mock(return_value=Element("button"))
    monkeypatch.setattr(flow, "Page", lambda _: page)
    monkeypatch.setattr(flow, "read_texts", Mock(side_effect=[["预约抢票"], [purchase_label]]))
    bot.open_sale()
    assert clock.wall == bot.window.start + 1
    assert not bot.first_click


def test_keep_queue_position(cfg, clock, monkeypatch):
    queue = SimpleNamespace(nodes=[Element("node", text="正在排队")])
    released = SimpleNamespace(nodes=[])
    bot = flow.DamaiBot(cfg, Mock())
    monkeypatch.setattr(flow, "Page", Mock(side_effect=[queue, released]))
    assert bot.active_page() is released
    bot.driver.back.assert_not_called()


def test_deadline_immediately_before_submit(cfg, clock):
    cfg.if_commit_order = True
    bot = flow.DamaiBot(cfg, Mock())
    clock.wall = bot.window.end - 1
    def click(node, before_click):
        clock.advance(2)
        before_click()
        pytest.fail("sent after deadline")
    page = Mock()
    page.click.side_effect = click
    page.named.return_value = [Element("button")]
    with pytest.raises(schedule.WindowExpired):
        bot.finish(page)
    assert bot.sent is False


@pytest.mark.parametrize("state", ["rejected", "disconnected", "created"])
def test_submission_outcomes(cfg, clock, monkeypatch, state):
    cfg.if_commit_order = True
    bot = flow.DamaiBot(cfg, Mock())
    clock.wall = bot.window.start
    page = Mock()
    page.named.return_value = [Element("button")]
    def click(node, before_click):
        before_click()
        if state == "disconnected":
            raise WebDriverException("network")
    page.click.side_effect = click
    result = Mock()
    texts = [cfg.keyword, "2026.12.26 19:00"]
    texts += ["订单创建失败", "库存不足"] if state == "rejected" else ["订单编号：123456"]
    result.nodes = [Element("text", text=v) for v in texts]
    result.named.side_effect = lambda *names: set(names).intersection(texts)
    result.texts.return_value = []
    monkeypatch.setattr(flow, "Page", lambda driver: result)
    if state == "rejected":
        with pytest.raises(schedule.NoStock):
            bot.finish(page)
        assert not bot.sent
    else:
        assert ("待核实" if state == "disconnected" else "订单已生成") in bot.finish(page)
        with pytest.raises(flow.PurchaseStopped):
            bot.finish(page)
    page.click.assert_called_once()


@pytest.mark.parametrize("result", ["订单已生成，请在大麦核对并付款", "演练完成，提交次数为零", "提交结果待核实"])
def test_single_notification_for_result(cfg, monkeypatch, result):
    from contextlib import nullcontext
    cfg.sale_at = datetime.now(schedule.BEIJING)
    monkeypatch.setattr(flow.sys, "argv", ["purchase", "--dry-run"])
    monkeypatch.setattr(flow.Config, "load_config", lambda _: cfg)
    monkeypatch.setattr(flow, "prepare_vision", nullcontext)
    monkeypatch.setattr(flow, "connect", Mock())
    factory = Mock()
    factory.return_value.run.return_value = result
    monkeypatch.setattr(flow, "DamaiBot", factory)
    notify = Mock(return_value=False)
    monkeypatch.setattr(flow, "notify", notify)
    assert flow.main() == (1 if "待核实" in result else 0)
    notify.assert_called_once()
    assert notify.call_args.args[0] == result


def test_timeout_notification(cfg, clock, monkeypatch):
    clock.wall = cfg.sale_at.timestamp() + 601
    monkeypatch.setattr(flow.sys, "argv", ["purchase", "--dry-run"])
    monkeypatch.setattr(flow.Config, "load_config", lambda _: cfg)
    notify = Mock()
    monkeypatch.setattr(flow, "notify", notify)
    monkeypatch.setattr(flow, "connect", Mock(side_effect=AssertionError))
    assert flow.main() == 1
    assert "时间已到" in notify.call_args.args[1]


def test_notification_failure_keeps_terminal_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(schedule.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("osascript", 5)))
    assert not schedule.notify("演练完成", "测试")
    assert "通知发送失败" in capsys.readouterr().err


@pytest.mark.parametrize("value", [None, 1900])
def test_invalid_session_time_type(tmp_path, value):
    data = json.loads(EXAMPLE.read_text())
    data["session_time"] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="session_time"):
        Config.load_config(path)


def test_stock_signal_has_priority_over_stale_price(cfg, clock):
    bot = flow.DamaiBot(cfg, Mock())
    page = Mock()
    page.named.return_value = [Element("node", text="所选票档已售罄")]
    page.texts.return_value = ["399"]
    bot.active_page = Mock(return_value=page)
    with pytest.raises(schedule.NoStock):
        bot.until(lambda p: "399" in p.texts("tv_price"), "选中票价")


@pytest.mark.parametrize("initial,target,adjustments", [
    (2, 2, []), (2, 3, ["img_jia"]), (3, 1, ["img_jian", "img_jian"]),
])
def test_retained_ticket_count(cfg, clock, monkeypatch, initial, target, adjustments):
    cfg.price = "看台399元"
    cfg.users = [str(i) for i in range(target)]
    bot = flow.DamaiBot(cfg, Mock())
    page = Mock()
    count = initial
    actions = []
    def texts(rid):
        return {"tv_title": [cfg.keyword], "tv_price": [str(count * 399)],
                "tv_num": [str(count) + "张"]}.get(rid, [])
    def click(node, before_click):
        nonlocal count
        before_click()
        actions.append(node.tag)
        count += int(node.tag == "img_jia") - int(node.tag == "img_jian")
    page.texts.side_effect = texts
    page.click.side_effect = click
    page.ids.side_effect = lambda rid: [Element(rid, enabled="true")]
    page.named.return_value = []
    bot.active_page = Mock(return_value=page)
    original = flow.WebDriverWait
    monkeypatch.setattr(flow, "WebDriverWait", lambda driver, seconds, **kw: original(driver, 0))
    def options(_, rid, session=None):
        if rid == "project_detail_perform_flowlayout":
            assert session == (cfg.date, cfg.session_time)
            return [(Element("session"), cfg.date + " " + cfg.session_time)]
        return [(Element("tier"), cfg.price)]
    monkeypatch.setattr(flow, "option_texts", options)
    bot.select_tickets()
    assert actions == ["session", "tier", *adjustments, "btn_buy_view"]
    assert count == target

import json
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree.ElementTree import Element, SubElement

import pytest
from selenium.common.exceptions import WebDriverException

from damai_appium import damai_app_v2 as flow
from damai_appium.config import Config
from damai_appium.device import select_device
from damai_appium.page import Page, PurchaseStopped, matches_session


@pytest.fixture
def bot():
    cfg = Config("http://localhost", "目标演出", ["观演人甲"], "哈尔滨", "2026-11-28",
                 "看台399元", 5, False, "19:00")
    return flow.DamaiBot(cfg, Mock())


def test_rehearsal_never_submits(bot):
    page = Mock()
    assert "提交次数为零" in bot.finish(page)
    page.click.assert_not_called()


def test_missing_button_cannot_report_success(bot):
    bot.config.if_commit_order = True
    page = Mock()
    page.named.return_value = []
    with pytest.raises(PurchaseStopped, match="提交按钮"):
        bot.finish(page)
    page.click.assert_not_called()


def test_lost_response_cannot_submit_again(bot):
    page = Mock()
    page.named.return_value = [Element("button")]
    page.click.side_effect = WebDriverException("disconnect")
    bot.config.if_commit_order = True
    assert "待核实" in bot.finish(page)
    with pytest.raises(PurchaseStopped):
        bot.finish(page)
    assert page.click.call_count == 1


@pytest.mark.parametrize("wanted_time,labels,expected", [
    ("19:00", ["订单号：123456", "目标演出", "2026.11.28 19:00"], "订单已生成"),
    ("19:00", ["库存不足"], "失败"),
    ("19:00", ["其他演出", "订单号：123456", "2026.11.28 19:00"], "待核实"),
    ("9:00", ["目标演出", "订单号：123456", "2026.11.28 19:00"], "待核实"),
])
def test_order_evidence(bot, monkeypatch, labels, expected, wanted_time):
    bot.config.session_time = wanted_time
    bot.config.if_commit_order = True
    page = Mock()
    page.named.return_value = [Element("button")]
    result = Mock()
    result.nodes = [Element("Text", text=s) for s in labels]
    result.named.side_effect = lambda *names: set(names).intersection(labels)
    result.texts.return_value = []
    monkeypatch.setattr(flow, "Page", lambda driver: result)
    original = flow.WebDriverWait
    monkeypatch.setattr(flow, "WebDriverWait", lambda driver, seconds: original(driver, 0))
    assert expected in bot.finish(page)
    page.click.assert_called_once()


@pytest.mark.parametrize("status,expected", [("立即购票", "buy"), ("待开售", "wait"), ("预约抢票", "wait")])
def test_sale_states(status, expected):
    assert flow.sale_state(status) == expected


def test_unknown_state_stops():
    with pytest.raises(PurchaseStopped):
        flow.sale_state("已结束")


def test_wait_then_buy_and_cancel(bot, monkeypatch):
    page = Mock()
    page.texts.side_effect = lambda first, *rest: ["目标演出"] if "title" in first else ["哈尔滨市 · 场馆"]
    page.ids.return_value = [Element("button")]
    monkeypatch.setattr(flow, "Page", lambda driver: page)
    statuses = iter([["待开售"], ["立即购票"]])
    monkeypatch.setattr(flow, "read_texts", lambda *_: next(statuses))
    monkeypatch.setattr(flow.time, "sleep", lambda _: None)
    bot.open_sale()
    page.click.assert_called_once()
    monkeypatch.setattr(flow, "read_texts", lambda *_: ["待开售"])
    monkeypatch.setattr(flow.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        bot.open_sale()
    page.click.assert_called_once()


def test_same_day_multiple_sessions():
    rows = [(Element("row"), "2026-11-28 14:00"), (Element("row"), "2026-11-28 19:00")]
    with pytest.raises(PurchaseStopped, match="2 项"):
        flow.pick(rows, lambda text: matches_session(text, "11.28", ""), "场次")
    assert flow.pick(rows, lambda text: matches_session(text, "11.28", "19:00"), "场次") is rows[1][0]


def test_presale_badge_reaches_price_list(bot, monkeypatch):
    from damai_appium import options

    group = Element("group")
    session = SubElement(group, "row", bounds="[58,688][1022,845]")
    SubElement(session, "badge", text="预售")
    page = Mock()
    page.ids.return_value = [group]
    page.texts.return_value = [bot.config.keyword]
    monkeypatch.setattr(options, "Page", lambda _: page)
    monkeypatch.setattr(options.platform, "system", lambda: "Darwin")
    vision = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(["2026-11-28 周六 19:00 预售"])))
    monkeypatch.setattr(options.subprocess, "run", vision)
    bot.until = Mock(side_effect=[page, PurchaseStopped("到达票档列表")])
    with pytest.raises(PurchaseStopped, match="到达票档列表"):
        bot.select_tickets()
    page.click.assert_called_once_with(session)
    assert bot.until.call_args.args[1] == "票档列表"
    assert vision.call_args.args[0][-1] == "58,688,1022,845"


def test_sold_out_and_reordered_tiers():
    row = Element("row", enabled="true")
    with pytest.raises(PurchaseStopped, match="不可购买"):
        flow.pick([(row, "看台399元 售罄")], lambda _: True, "票档")
    assert flow.pick([(Element("row"), "内场999元"), (row, "看台399元")],
                     lambda text: flow.price_key(text) == "看台399", "票档") is row


@pytest.mark.parametrize("native,recognized,requested_time,expected", [
    (["2026-11-28 19:00"], [], "19:00", 0),
    (["2026-11-28"], [], "", 0),
    (["2026-11-28", "2026-11-28"], ["14:00", "19:00"], "19:00", 1),
    (["11.28 19:00"], ["2026-11-28"], "19:00", 0),
    (["2026-11-29"], ["2026-11-28 19:00"], "19:00", "冲突"),
    (["14:00"], ["2026-11-28 19:00"], "19:00", "冲突"),
    (["缺货"], ["2026-11-28 19:00"], "19:00", "不可购买"),
    (["预售", "预售"], ["2026-11-28 19:00"] * 2, "19:00", "2 项"),
    (["预售"], ["预售"], "19:00", "无法识别"),
    (["2026-11-28 14:00"], [], "19:00", "0 项"),
])
def test_session_rows(native, recognized, requested_time, expected, monkeypatch):
    from damai_appium import options

    rows = [Element("row", text=text, bounds=f"[0,{i}][100,{i+1}]")
            for i, text in enumerate(native)]
    page = Mock()
    monkeypatch.setattr(options.platform, "system", lambda: "Darwin")
    vision = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(recognized)))
    monkeypatch.setattr(options.subprocess, "run", vision)
    target = ("2026-11-28", requested_time)

    def pick_session():
        values = options.read_texts(page, rows, target)
        return flow.pick(zip(rows, values), lambda value: matches_session(value, *target), "目标场次")

    if isinstance(expected, int):
        assert pick_session() is rows[expected]
    else:
        failure = flow.NoStock if expected == "不可购买" else PurchaseStopped
        with pytest.raises(failure, match=expected):
            pick_session()
    assert vision.call_count == int(bool(recognized))
    assert page.driver.get_screenshot_as_png.call_count == vision.call_count


@pytest.mark.parametrize("listing", ["", "a unauthorized", "a device\nb device"])
def test_device_ambiguity(listing):
    with pytest.raises(ValueError):
        select_device("List of devices attached\n" + listing)


def test_select_requested_device():
    assert select_device("List of devices attached\na device\nb device", "b") == "b"


def test_old_configuration_without_index_or_time(tmp_path):
    import json
    path = tmp_path / "old.json"
    data = dict(server_url="http://localhost", keyword="标题", users=["甲"], city="哈尔滨",
                date="11.28", price="399元", if_commit_order=False)
    path.write_text(json.dumps(data))
    config = Config.load_config(path)
    assert config.session_time == "" and config.price_index is None
    data["if_commit_order"] = "false"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Config.load_config(path)


@pytest.mark.parametrize("name,price,quantity,outcome", [
    ("观演人甲", "399.00", "1", True),
    ("观演人乙", "399.00", "1", False),
    ("观演人甲", "599.00", "1", False),
    ("观演人甲", "399.00", "2", False),
])
def test_confirmation_without_toggling_preselected_viewer(bot, monkeypatch, name, price, quantity, outcome):
    from xml.etree.ElementTree import SubElement, tostring
    root = Element("hierarchy")
    def node(parent, value="", rid="", **attrs):
        return SubElement(parent, "node", {"text": value, "resource-id": "cn.damai:id/" + rid,
                                          "package": "cn.damai", **attrs})
    node(root, "确认购买", "order_activity_title")
    for text in ("目标演出", "2026.11.28 19:00", f"￥{price}票档", f"×{quantity}张"):
        node(root, text)
    row = node(root)
    node(row, name, "text_name")
    node(row, rid="checkbox", checkable="true", checked="true")
    page = Page(SimpleNamespace(current_package="cn.damai", page_source=tostring(root)))
    page.click = Mock()
    monkeypatch.setattr(bot, "until", lambda predicate, description: page)
    if outcome:
        assert bot.confirm() is page
    else:
        with pytest.raises(PurchaseStopped):
            bot.confirm()
    page.click.assert_not_called()


def test_unreadable_ticket_text_stops(monkeypatch):
    from damai_appium import options
    row = Element("row", bounds="[0,0][100,100]")
    page = Mock()
    monkeypatch.setattr(options.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(options.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'[""]'))
    with pytest.raises(PurchaseStopped, match="无法识别"):
        options.read_texts(page, [row])


def test_wrong_show_stops_before_purchase(bot, monkeypatch):
    page = Mock()
    page.texts.return_value = ["其他演出"]
    monkeypatch.setattr(flow, "Page", lambda driver: page)
    with pytest.raises(PurchaseStopped, match="演出全名"):
        bot.open_sale()
    page.click.assert_not_called()


def test_morning_selection_rejects_evening_only(bot, monkeypatch):
    bot.config.session_time = "9:00"
    page = Mock()
    page.texts.return_value = [bot.config.keyword]
    monkeypatch.setattr(bot, "until", lambda *_: page)
    monkeypatch.setattr(flow, "option_texts", lambda *_: [(Element("row"), "2026-11-28 19:00")])
    with pytest.raises(PurchaseStopped, match="目标场次"):
        bot.select_tickets()
    page.click.assert_not_called()


def test_morning_confirmation_rejects_evening_only(bot, monkeypatch):
    bot.config.session_time = "9:00"
    page = Mock()
    page.nodes = [Element("Text", text=v) for v in ("目标演出", "2026-11-28 19:00", "￥399.00票档", "×1张")]
    monkeypatch.setattr(bot, "until", lambda *_: page)
    monkeypatch.setattr(flow, "viewer_states", lambda _: {"观演人甲": Element("box", checked="true")})
    with pytest.raises(PurchaseStopped, match="确认页场次"):
        bot.confirm()


def test_vision_timeout_stops_purchase(monkeypatch):
    from damai_appium import options
    page = Mock()
    row = Element("row", bounds="[0,0][100,100]")
    timeout = options.subprocess.TimeoutExpired("swift", 45)
    monkeypatch.setattr(options.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(options.subprocess, "run", Mock(side_effect=timeout))
    with pytest.raises(PurchaseStopped, match="识别超时，请人工核对") as stopped:
        options.read_texts(page, [row])
    assert stopped.value.__cause__ is timeout
    page.click.assert_not_called()


@pytest.mark.parametrize("requested,shown,expected", [
    ("9:00", "09:00", True),
    ("09:00", "9：00", True),
    ("9:00", "19:00", False),
    ("19:00", "19:01", False),
    ("19:00", "19:000", False),
    ("9:00", "19:00 / 09:00", False),
    ("", "", True),
])
def test_session_clock(requested, shown, expected):
    assert matches_session("2026.11.28 周六 " + shown, "2026-11-28", requested) is expected

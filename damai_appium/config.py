import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class Config:
    server_url: str
    keyword: str
    users: List[str]
    city: str
    date: str
    price: str
    price_index: Optional[int]
    if_commit_order: bool
    session_time: str = ""
    sale_at: Optional[datetime] = None
    retry_minutes: int = 5

    @classmethod
    def load_config(cls, path=None):
        source = Path(path) if path else Path(__file__).with_name("config.jsonc")
        data = json.loads(source.read_text(encoding="utf-8"))
        for field in ("server_url", "keyword", "city", "date", "price"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise ValueError(f"配置缺少有效的 {field}")
        users = data.get("users")
        if not isinstance(users, list) or not users or not all(isinstance(user, str) and user.strip() for user in users):
            raise ValueError("users 需要至少一位观演人")
        users = [user.strip() for user in users]
        if len(set(users)) != len(users):
            raise ValueError("观演人姓名重复，无法区分")
        session_time = data.get("session_time", "")
        if not isinstance(session_time, str):
            raise ValueError("session_time 必须为字符串，例如 19:00")
        submit = data.get("if_commit_order", False)
        if type(submit) is not bool:
            raise ValueError("if_commit_order 必须为 true 或 false")
        count = data.get("ticket_count", len(users))
        if type(count) is not int or count != len(users):
            raise ValueError("ticket_count 必须与实名观演人数一致")
        minutes = data.get("retry_minutes", 5)
        if type(minutes) is not int or not 5 <= minutes <= 10:
            raise ValueError("retry_minutes 必须为 5 至 10 的整数")
        sale_at = None
        if "sale_at" in data:
            try:
                sale_at = datetime.strptime(data["sale_at"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone(timedelta(hours=8)))
            except (TypeError, ValueError) as exc:
                raise ValueError("sale_at 请填写北京时间 YYYY-MM-DD HH:MM:SS") from exc
        elif "retry_minutes" in data:
            raise ValueError("配置重试窗口时需要填写 sale_at")
        return cls(data["server_url"].rstrip("/"), data["keyword"].strip(), users,
                   data["city"].strip(), data["date"].strip(), data["price"].strip(),
                   data.get("price_index"), submit, session_time.strip(), sale_at, minutes)

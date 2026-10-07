"""Тесты коннектора Мегафон: CRM API подменяется примерами ответов из документации."""
from __future__ import annotations

import httpx
import pytest

import megafon_connector
from infopanel_connect import Condition, ConfigError, Context
from infopanel_connect.testing import check_connector
from megafon_connector import MegafonConnector

HISTORY = [
    {"uid": "3934307521", "type": "in", "status": "missed", "client": "79263808397",
     "destination": "group", "user": "", "user_name": "", "group_name": "Отдел продаж",
     "diversion": "74954010121", "start": "2022-01-20T08:59:22Z", "wait": 13, "duration": 0,
     "record": ""},
    {"uid": "1755936870", "type": "in", "status": "success", "client": "79263808397",
     "user": "admin", "user_name": "Администратор", "group_name": "",
     "diversion": "74954010121", "start": "2022-01-20T08:58:42Z", "wait": 5, "duration": 23,
     "record": ""},
]

USERS = {
    "items": [
        {"login": "admin", "name": "Администратор", "position": "Администратор", "ext": "701",
         "telnum": "", "email": "", "role": "admin", "mobile": "",
         "mobile_redirect": {"enabled": False}, "status": "online"},
        {"login": "director", "name": "Директор", "position": "Директор", "ext": "703",
         "telnum": "", "email": "", "role": "admin", "mobile": "",
         "mobile_redirect": {"enabled": False}, "status": "online"},
        {"login": "user1", "name": "Пользователь", "position": "Менеджер", "telnum": "",
         "ext": "702", "email": "", "role": "user", "mobile": "79261904198",
         "mobile_redirect": {"enabled": True, "forward": False, "delay": 15}, "status": "offline"},
    ],
    "info": {"search": "", "start": 0, "limit": 3, "total": 3},
}

GROUPS = {
    "items": [
        {"id": "g_0a922987-97ac-4f45-9e47-59a27d23c2cb", "name": "Отдел поставок", "ext": "704",
         "call_order": "EVENLY", "call_duration": 10,
         "users": [
             {"login": "admin", "calls_enable": True, "group_head": False,
              "is_absence_active": False, "is_forwarding_disabled": False},
             {"login": "ivan", "calls_enable": False, "group_head": False,
              "is_absence_active": True, "is_forwarding_disabled": False},
         ],
         "timeout": {"time": 120, "target": "voicemail"}, "advanced": "off", "queue_position": False},
        {"id": "sales", "name": "Отдел продаж", "ext": "700", "call_order": "BYORDER",
         "call_duration": 10,
         "users": [
             {"login": "admin", "calls_enable": True, "group_head": False,
              "is_absence_active": False, "is_forwarding_disabled": False},
             {"login": "manager", "calls_enable": True, "group_head": False,
              "is_absence_active": False, "is_forwarding_disabled": False},
             {"login": "ivan", "calls_enable": False, "group_head": False,
              "is_absence_active": False, "is_forwarding_disabled": False},
         ],
         "timeout": {"time": 120, "target": "voicemail"}, "advanced": "off", "queue_position": False},
    ],
    "info": {"search": "", "start": 0, "limit": 2, "total": 2},
}


@pytest.fixture
def api(monkeypatch):
    """Подменяет egress.fetch; возвращает список запросов (path, params, headers)."""
    calls: list[tuple] = []
    answers = {
        megafon_connector.HISTORY_PATH: HISTORY,
        megafon_connector.USERS_PATH: USERS,
        megafon_connector.GROUPS_PATH: GROUPS,
    }

    async def fake_fetch(method, url, *, params=None, headers=None, **_):
        path = httpx.URL(url).path
        calls.append((path, dict(params or {}), dict(headers or {})))
        return httpx.Response(200, json=answers[path])

    monkeypatch.setattr(megafon_connector.egress, "fetch", fake_fetch)
    return calls


def ctx() -> Context:
    return Context(MegafonConnector(), settings={}, secrets={"api_key": "k"})


async def test_check_passes(api):
    report = await check_connector(MegafonConnector(), secrets={"api_key": "k"})
    assert report.ok, report.text()


async def test_calls_and_period(api):
    conditions = (
        Condition("date", "gte", "2022-01-01T00:00:00+03:00"),
        Condition("date", "lt", "2022-02-01T00:00:00+03:00"),
    )
    result = await MegafonConnector().fetch(ctx(), "calls", conditions)
    path, params, headers = api[0]
    assert params == {"start": "20211231T210000Z", "end": "20220131T210000Z"}
    assert headers["X-API-KEY"] == "k"
    assert result.applied == conditions
    missed, success = result.rows
    assert missed["operator_login"] is None and missed["group_name"] == "Отдел продаж"
    assert missed["record_url"] is None and missed["wait"] == 13
    assert success["operator_name"] == "Администратор" and success["group_name"] is None
    assert success["duration"] == 23


async def test_operators(api):
    rows = (await MegafonConnector().fetch(ctx(), "operators", ())).rows
    assert api[0][1] == {"with": "status"}
    admin, director, user1 = rows
    assert admin["email"] is None and admin["status"] == "online"
    assert (admin["mobile_redirect"], admin["mobile_redirect_forward"], admin["mobile_redirect_delay"]) == (0, None, None)
    assert user1["mobile"] == "79261904198" and user1["status"] == "offline"
    assert (user1["mobile_redirect"], user1["mobile_redirect_forward"], user1["mobile_redirect_delay"]) == (1, 0, 15)


async def test_groups_and_members(api):
    groups = (await MegafonConnector().fetch(ctx(), "groups", ())).rows
    assert groups[0]["timeout_time"] == 120 and groups[0]["timeout_target"] == "voicemail"
    assert [g["users_count"] for g in groups] == [2, 3]
    members = (await MegafonConnector().fetch(ctx(), "group_members", ())).rows
    assert [(m["group_id"], m["login"]) for m in members] == [
        ("g_0a922987-97ac-4f45-9e47-59a27d23c2cb", "admin"),
        ("g_0a922987-97ac-4f45-9e47-59a27d23c2cb", "ivan"),
        ("sales", "admin"), ("sales", "manager"), ("sales", "ivan"),
    ]
    assert members[1]["is_absence_active"] == 1 and members[1]["calls_enable"] == 0


async def test_lists_are_paged(monkeypatch):
    pages = {0: GROUPS["items"][:1], 1: GROUPS["items"][1:]}
    seen = []

    async def fake_fetch(method, url, *, params=None, **_):
        start = int((params or {}).get("start", 0))
        seen.append(dict(params or {}))
        return httpx.Response(200, json={"items": pages[start], "info": {"start": start, "limit": 1, "total": 2}})

    monkeypatch.setattr(megafon_connector.egress, "fetch", fake_fetch)
    rows = (await MegafonConnector().fetch(ctx(), "groups", ())).rows
    assert [r["id"] for r in rows] == ["g_0a922987-97ac-4f45-9e47-59a27d23c2cb", "sales"]
    assert seen == [{}, {"start": 1, "limit": 1}]


async def test_bad_key(monkeypatch):
    async def fake_fetch(*_, **__):
        return httpx.Response(401)

    monkeypatch.setattr(megafon_connector.egress, "fetch", fake_fetch)
    with pytest.raises(ConfigError):
        await MegafonConnector().test(ctx())

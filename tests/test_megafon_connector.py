"""Тесты коннектора Мегафон: API подменяется примерами ответов из документации."""
from __future__ import annotations

import httpx
try:
    import pytest
except ImportError:
    class _Pytest:
        @staticmethod
        def fixture(fn):
            return fn

        @staticmethod
        def raises(exc):
            class _Raises:
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc_val, exc_tb):
                    return exc_type is not None and issubclass(exc_type, exc)
            return _Raises()
    pytest = _Pytest()

import megafon_connector
from infopanel_connect import Condition, ConfigError, Context
from infopanel_connect.testing import check_connector
from megafon_connector import MegafonConnector

# --------------------------------------------------------------------------- #
# Mocks для ВАТС 1.0 (legacy)
# --------------------------------------------------------------------------- #

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

# --------------------------------------------------------------------------- #
# Mocks для ВАТС 2.0 (simple-api / data-api)
# --------------------------------------------------------------------------- #

REPORTS_LIST_V2 = {
    "data": [
        {"id": 1015, "name": "История звонков", "type": "mgf_calls"},
    ]
}

CALLS_V2 = {
    "data": [
        {
            "communication_id": 3934307521,
            "call_start_date_time": "2022-01-20 08:59:22",
            "call_direction": {"value_id": "in", "value": "Входящий звонок"},
            "call_status": {"value_id": "lost", "value": "Пропущенный"},
            "contact_phone_number": {"value": "79263808397"},
            "virtual_phone_number": "74954010121",
            "virtual_number_comment": None,
            "employees": [],
            "received_call": None,
            "call_talk_duration": "00:00:00",
            "call_total_duration": "00:00:13",
            "call_await_talk_duration": "00:00:13",
            "call_records": None,
            "employee_rating": None,
            "communication_comment": None,
            "call_finish_reason": {"value": "Не дозвонились", "value_id": "no_answer"},
        },
        {
            "communication_id": 1755936870,
            "call_start_date_time": "2022-01-20 08:58:42",
            "call_direction": {"value_id": "in", "value": "Входящий звонок"},
            "call_status": {"value_id": "received", "value": "Принятый"},
            "contact_phone_number": {"value": "79263808397"},
            "virtual_phone_number": "74954010121",
            "virtual_number_comment": None,
            "employees": [{"value": "Администратор"}],
            "received_call": "Администратор",
            "call_talk_duration": "00:00:23",
            "call_total_duration": "00:00:28",
            "call_await_talk_duration": "00:00:05",
            "call_records": None,
            "employee_rating": None,
            "communication_comment": None,
            "call_finish_reason": None,
        },
    ],
    "metadata": {"total_items": 2},
}

EMPLOYEES_V2 = {
    "items": [
        {"id": 1, "login": "admin", "first_name": "Администратор", "last_name": "", "position_name": "Администратор", "short_phone": "701",
         "sim_phone": None, "email": None, "role_name": "admin", "phone": None,
         "is_phone_dialing_enabled": False, "is_sip_registered": True},
        {"id": 2, "login": "director", "first_name": "Директор", "last_name": "", "position_name": "Директор", "short_phone": "703",
         "sim_phone": None, "email": None, "role_name": "admin", "phone": None,
         "is_phone_dialing_enabled": False, "is_sip_registered": True},
        {"id": 3, "login": "user1", "first_name": "Пользователь", "last_name": "", "position_name": "Менеджер", "short_phone": "702",
         "sim_phone": None, "email": None, "role_name": "user", "phone": "79261904198",
         "is_phone_dialing_enabled": True, "phone_dialing_type": None, "phone_delay": 15, "is_sip_registered": False},
    ],
    "info": {"start": 0, "limit": 3, "total": 3},
}

GROUPS_V2 = [
    {
        "id": "10",
        "name": "Отдел поставок",
        "short_phone": "704",
        "distribution_type": "evenly",
        "distribution_delay": 10,
        "employees": [
            {"employee_id": 1, "is_active": True},
            {"employee_id": 99, "is_active": False},
        ],
        "fail_dialing_action": "voicemail",
        "fail_dialing_timeout": 120,
    },
    {
        "id": "sales",
        "name": "Отдел продаж",
        "short_phone": "700",
        "distribution_type": "in_turn",
        "distribution_delay": 10,
        "employees": [
            {"employee_id": 1, "is_active": True},
            {"employee_id": 2, "is_active": True},
            {"employee_id": 3, "is_active": True},
        ],
        "fail_dialing_action": "voicemail",
        "fail_dialing_timeout": 120,
    },
]


@pytest.fixture
def api(monkeypatch):
    """Подменяет egress.fetch; возвращает список запросов."""
    calls: list[tuple] = []

    async def fake_fetch(method, url, *, params=None, headers=None, json=None, **_):
        u = httpx.URL(url)
        path = u.path
        calls.append((path, dict(params or {}), dict(headers or {}), json))

        if path == "/v2.0":
            rpc_m = (json or {}).get("method")
            if rpc_m == "get.reports_list":
                return httpx.Response(200, json={"jsonrpc": "2.0", "result": REPORTS_LIST_V2})
            if rpc_m == "get.mgf_calls":
                return httpx.Response(200, json={"jsonrpc": "2.0", "result": CALLS_V2})
            return httpx.Response(404)
        if path == "/employee":
            return httpx.Response(200, json=EMPLOYEES_V2)
        if path == "/employee-group":
            return httpx.Response(200, json=GROUPS_V2)

        # Legacy ВАТС 1.0
        if path == megafon_connector.HISTORY_PATH:
            return httpx.Response(200, json=HISTORY)
        if path == "/crmapi/v1/users":
            return httpx.Response(200, json=USERS)
        if path == "/crmapi/v1/groups":
            return httpx.Response(200, json=GROUPS)

        return httpx.Response(404)

    monkeypatch.setattr(megafon_connector.egress, "fetch", fake_fetch)
    return calls


def ctx_v1() -> Context:
    return Context(MegafonConnector(), settings={"api_domain": "api.megapbx.ru"}, secrets={"api_key": "k"})


def ctx_v2() -> Context:
    return Context(MegafonConnector(), settings={}, secrets={"api_key": "k"})


async def test_check_passes(api):
    report = await check_connector(MegafonConnector(), secrets={"api_key": "k"})
    assert report.ok, report.text()


async def test_calls_v2(api):
    conditions = (
        Condition("date", "gte", "2022-01-01T00:00:00+03:00"),
        Condition("date", "lt", "2022-02-01T00:00:00+03:00"),
    )
    result = await MegafonConnector().fetch(ctx_v2(), "calls", conditions)
    assert result.applied == conditions
    missed, success = result.rows
    assert missed["operator_login"] is None
    assert missed["wait"] == 13
    assert missed["status"] == "missed"
    assert success["operator_name"] == "Администратор"
    assert success["operator_login"] == "admin"
    assert success["duration"] == 23
    assert success["status"] == "success"


async def test_calls_v1(api):
    conditions = (
        Condition("date", "gte", "2022-01-01T00:00:00+03:00"),
        Condition("date", "lt", "2022-02-01T00:00:00+03:00"),
    )
    result = await MegafonConnector().fetch(ctx_v1(), "calls", conditions)
    missed, success = result.rows
    assert missed["operator_login"] is None and missed["group_name"] == "Отдел продаж"
    assert missed["wait"] == 13
    assert success["operator_name"] == "Администратор"
    assert success["duration"] == 23


async def test_operators_v2(api):
    rows = (await MegafonConnector().fetch(ctx_v2(), "operators", ())).rows
    admin, director, user1 = rows
    assert admin["email"] is None and admin["status"] == "online"
    assert (admin["mobile_redirect"], admin["mobile_redirect_forward"], admin["mobile_redirect_delay"]) == (0, None, None)
    assert user1["mobile"] == "79261904198" and user1["status"] == "offline"
    assert (user1["mobile_redirect"], user1["mobile_redirect_delay"]) == (1, 15)


async def test_groups_and_members_v2(api):
    groups = (await MegafonConnector().fetch(ctx_v2(), "groups", ())).rows
    assert groups[0]["timeout_time"] == 120 and groups[0]["timeout_target"] == "voicemail"
    assert [g["users_count"] for g in groups] == [2, 3]

    members = (await MegafonConnector().fetch(ctx_v2(), "group_members", ())).rows
    assert [(m["group_id"], m["login"]) for m in members] == [
        ("10", "admin"),
        ("10", "99"),
        ("sales", "admin"),
        ("sales", "director"),
        ("sales", "user1"),
    ]
    assert members[0]["calls_enable"] == 1
    assert members[1]["calls_enable"] == 0


async def test_bad_key(monkeypatch):
    async def fake_fetch(*_, **__):
        return httpx.Response(401)

    monkeypatch.setattr(megafon_connector.egress, "fetch", fake_fetch)
    with pytest.raises(ConfigError):
        await MegafonConnector().test(ctx_v2())

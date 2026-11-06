"""Коннектор Инфопанели для телефонии Мегафон (виртуальная АТС).

Поддерживает Мегафон ВАТС 2.0 (домены *.cp.megafon.ru, авторизация JWT-токеном):
- звонки — JSON-RPC 2.0 метод ``get.mgf_calls`` к ``https://data-api.cp.megafon.ru/v2.0``;
- сотрудники — ``GET https://simple-api.cp.megafon.ru/employee``;
- отделы и их состав — ``GET https://simple-api.cp.megafon.ru/employee-group``.

А также legacy ВАТС 1.0 (домен api.megapbx.ru):
- звонки — ``GET /crmapi/v1/history/json``;
- сотрудники — ``GET /crmapi/v1/users?with=status``;
- отделы и их состав — ``GET /crmapi/v1/groups``.

Запуск из корня репозитория (PowerShell):

    $env:INFOPANEL_SIGNING_SECRET = "dev"
    infopanel-connect serve megafon_connector:MegafonConnector

Проверка без платформы (secrets.json — {"api_key": "<ключ>"}):

    infopanel-connect check megafon_connector:MegafonConnector --secrets secrets.json
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from infopanel_connect import (
    Column,
    Condition,
    ConfigError,
    Connector,
    Context,
    Entity,
    FetchResult,
    Field,
    UpstreamError,
)
from infopanel_connect import egress

# Автоматически разрешаем домены ВАТС 2.0 в механизме защиты SSRF
_needed_hosts = {"simple-api.cp.megafon.ru", "data-api.cp.megafon.ru"}
_cur_hosts = {h.strip().lower() for h in os.environ.get("INFOPANEL_EGRESS_ALLOW_HOSTS", "").split(",") if h.strip()}
if not _needed_hosts.issubset(_cur_hosts):
    os.environ["INFOPANEL_EGRESS_ALLOW_HOSTS"] = ",".join(_cur_hosts | _needed_hosts)

# --------------------------------------------------------------------------- #
# Сущности (таблицы), которые коннектор отдаёт платформе.
# id уникален в пределах источника; подпись видит пользователь.
# --------------------------------------------------------------------------- #

CALLS = Entity("calls", "Звонки")
OPERATORS = Entity("operators", "Сотрудники")
GROUPS = Entity("groups", "Отделы")
GROUP_MEMBERS = Entity("group_members", "Состав отделов")

# --------------------------------------------------------------------------- #
# Пути и параметры API
# --------------------------------------------------------------------------- #

HISTORY_PATH = "/crmapi/v1/history/json"
USERS_PATH = "/employee"
GROUPS_PATH = "/employee-group"
DATE_COLUMN = "date"  # колонка, по которой приходит период датасета
DEFAULT_DOMAIN = "simple-api.cp.megafon.ru"  # REST API Мегафон ВАТС 2.0
DATA_API_DOMAIN = "data-api.cp.megafon.ru"  # JSON-RPC Data API Мегафон ВАТС 2.0
RPC_PATH = "/v2.0"
MAX_PAGES = 1000  # предохранитель от бесконечного обхода страниц
PAGE_LIMIT = 1000  # размер страницы при выборке звонков

CALL_FIELDS = [
    "communication_id",
    "call_start_date_time",
    "contact_phone_number",
    "call_direction",
    "call_status",
    "call_type",
    "virtual_phone_number",
    "virtual_number_comment",
    "employees",
    "received_call",
    "call_total_duration",
    "call_talk_duration",
    "call_await_talk_duration",
    "call_is_internal",
    "call_finish_reason",
    "employee_rating",
    "call_records",
    "communication_comment",
]

# missedStatus из ответа: 1 — клиент перезвонил, 2 — перезвонили,
# 3 — не перезванивали, 4 — не дозвонились.
MISSED_STATUS: dict[int, str] = {
    1: "Клиент перезвонил",
    2: "Перезвонили",
    3: "Не перезванивали",
    4: "Не дозвонились",
}

# --------------------------------------------------------------------------- #
# Колонки сущностей: имена — для платформы, подписи — для людей.
# Ключи строк из fetch обязаны совпадать с этими именами. Логического типа у
# колонок нет — флаги отдаём числами 1/0 (сумма по колонке — количество «да»).
# --------------------------------------------------------------------------- #

COLUMNS: dict[str, list[Column]] = {
    "calls": [
        Column("id", "ID звонка", "string"),
        Column("date", "Дата и время начала", "datetime"),
        Column("direction", "Направление (in/out)", "string"),
        Column("status", "Статус (success/missed/noanswer)", "string"),
        Column("client_number", "Номер клиента", "string"),
        Column("diversion", "Номер ВАТС, по которому пришёл звонок", "string"),
        Column("telnum_name", "Имя номера ВАТС", "string"),
        Column("destination", "Адресат входящего звонка", "string"),
        Column("operator_login", "Логин сотрудника", "string"),
        Column("operator_name", "Сотрудник", "string"),
        Column("group_name", "Отдел", "string"),
        Column("wait", "Ожидание на линии, сек", "number"),
        Column("duration", "Длительность разговора, сек", "number"),
        Column("record_url", "Ссылка на запись разговора", "string"),
        Column("rating", "Оценка качества обслуживания", "number"),
        Column("note", "Примечание", "string"),
        Column("missed_status", "Статус пропущенного звонка", "string"),
    ],
    "operators": [
        Column("login", "Логин", "string"),
        Column("name", "Имя", "string"),
        Column("position", "Должность", "string"),
        Column("email", "Электронная почта", "string"),
        Column("ext", "Внутренний номер", "string"),
        Column("telnum", "Прямой номер", "string"),
        Column("mobile", "Мобильный телефон", "string"),
        Column("role", "Права в системе", "string"),
        Column("status", "Статус регистрации (на момент выгрузки)", "string"),
        Column("mobile_redirect", "Переадресация на мобильный", "number"),
        Column("mobile_redirect_forward", "Переадресация на мобильный: forward", "number"),
        Column("mobile_redirect_delay", "Задержка переадресации на мобильный, сек", "number"),
    ],
    "groups": [
        Column("id", "ID отдела", "string"),
        Column("name", "Отдел", "string"),
        Column("ext", "Внутренний номер", "string"),
        Column("call_order", "Распределение звонков", "string"),
        Column("call_duration", "Интервал дозвона, сек", "number"),
        Column("timeout_time", "Если никто не отвечает: через, сек", "number"),
        Column("timeout_target", "Если никто не отвечает: куда", "string"),
        Column("advanced", "Для нетерпеливых клиентов", "string"),
        Column("queue_position", "Сообщать номер в очереди", "number"),
        Column("users_count", "Сотрудников в отделе", "number"),
    ],
    "group_members": [
        Column("group_id", "ID отдела", "string"),
        Column("group_name", "Отдел", "string"),
        Column("login", "Логин сотрудника", "string"),
        Column("calls_enable", "Принимает звонки отдела", "number"),
        Column("group_head", "Руководитель отдела", "number"),
        Column("is_absence_active", "Отсутствует", "number"),
        Column("is_forwarding_disabled", "Переадресация отключена", "number"),
    ],
}


# --------------------------------------------------------------------------- #
# Помощники: домен, разбор дат и строк
# --------------------------------------------------------------------------- #

def _domain(raw: str) -> str:
    """Домен из формы: терпим «https://ats.x.ru/», оставляем «ats.x.ru»."""
    text = str(raw or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].strip()
    if not text or "@" in text or ":" in text:
        raise ConfigError("«Домен АТС» — имя сервера, например simple-api.cp.megafon.ru")
    return text


def _data_api_domain(domain: str) -> str:
    """Вычисляет домен JSON-RPC data-api по REST домену."""
    if domain.startswith("simple-api."):
        return "data-api." + domain[len("simple-api."):]
    if "megapbx.ru" in domain:
        return domain
    return DATA_API_DOMAIN


def _is_v1(domain: str) -> bool:
    """Проверяет, относится ли домен к устаревшей ВАТС 1.0 (megapbx.ru)."""
    return "megapbx.ru" in domain


def _parse_duration(val) -> int | None:
    """Переводит 'hh:mm:ss' или числовое значение в секунды."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    val = str(val).strip()
    parts = val.split(":")
    if len(parts) == 3:
        try:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        except ValueError:
            pass
    return _number(val)


def _megafon_dt_v1(value) -> str | None:
    """«2026-01-31T00:00:00+03:00» → «20260130T210000Z» (UTC, ВАТС 1.0)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _period_v1(conditions: tuple[Condition, ...]):
    """Период датасета для ВАТС 1.0."""
    start = end = None
    applied: list[Condition] = []
    for cond in conditions:
        if cond.field != DATE_COLUMN or cond.op not in ("gte", "lt"):
            continue
        value = _megafon_dt_v1(cond.value)
        if not value:
            continue
        if cond.op == "gte" and start is None:
            start = value
            applied.append(cond)
        elif cond.op == "lt" and end is None:
            end = value
            applied.append(cond)
    return start, end, tuple(applied)


def _parse_moment(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _period_v2(conditions: tuple[Condition, ...]):
    """Период датасета ВАТС 2.0 (gte начала, lt/lte конца по колонке date) → date_from, date_till."""
    start_dt = end_dt = None
    applied: list[Condition] = []
    for cond in conditions:
        if cond.field != DATE_COLUMN or cond.op not in ("gte", "lt", "lte"):
            continue
        moment = _parse_moment(cond.value)
        if not moment:
            continue
        if cond.op == "gte" and start_dt is None:
            start_dt = moment
            applied.append(cond)
        elif cond.op in ("lt", "lte") and end_dt is None:
            end_dt = moment
            applied.append(cond)

    now = datetime.now()
    if start_dt is None and end_dt is None:
        end_dt = now
        start_dt = end_dt - timedelta(days=30)
    elif start_dt is None and end_dt is not None:
        start_dt = end_dt - timedelta(days=30)
    elif end_dt is None and start_dt is not None:
        end_dt = min(now, start_dt + timedelta(days=30))

    # Ограничение API Мегафона: не более 366 дней между датами
    if (end_dt - start_dt).days > 365:
        start_dt = end_dt - timedelta(days=365)

    date_from = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    date_till = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    return date_from, date_till, tuple(applied)


def _call_row(item: dict, name_to_login: dict | None = None) -> dict:
    """Звонок → строка с ключами из COLUMNS["calls"]."""
    uid = item.get("communication_id") if item.get("communication_id") is not None else item.get("uid")

    start = item.get("call_start_date_time") or item.get("start")
    if start and isinstance(start, str) and " " in start:
        start = start.replace(" ", "T")

    call_dir = item.get("call_direction")
    if isinstance(call_dir, dict):
        direction = call_dir.get("value_id") or call_dir.get("value")
    else:
        direction = call_dir or item.get("type")

    call_stat = item.get("call_status")
    if isinstance(call_stat, dict):
        stat_raw = str(call_stat.get("value_id") or call_stat.get("value") or "").lower()
    else:
        stat_raw = str(call_stat or item.get("status") or "").lower()

    if stat_raw in ("received", "success", "принятый", "принят"):
        status = "success"
    elif stat_raw in ("lost", "missed", "потерянный", "пропущенный"):
        status = "missed"
    elif stat_raw:
        status = stat_raw
    else:
        status = None

    client = item.get("contact_phone_number")
    if isinstance(client, dict):
        client = client.get("value")
    else:
        client = client or item.get("client")

    employees = item.get("employees")
    op_name = None
    if isinstance(employees, list) and employees:
        first = employees[0]
        if isinstance(first, dict):
            op_name = first.get("value") or first.get("name")
        elif isinstance(first, str):
            op_name = first
    if not op_name:
        op_name = item.get("received_call") or item.get("user_name")
    op_name = _text(op_name)

    op_login = item.get("user")
    if not op_login and op_name and name_to_login:
        op_login = name_to_login.get(op_name)
    op_login = _text(op_login)

    talk_dur = _parse_duration(item.get("call_talk_duration"))
    total_dur = _parse_duration(item.get("call_total_duration"))
    dur = talk_dur if talk_dur is not None else _parse_duration(item.get("duration"))
    if dur is None:
        dur = total_dur

    wait_val = _parse_duration(item.get("call_await_talk_duration"))
    if wait_val is None:
        wait_val = _number(item.get("wait"))

    records = item.get("call_records")
    rec_url = None
    if isinstance(records, dict):
        links = records.get("links")
        if isinstance(links, list) and links:
            rec_url = links[0]
    elif isinstance(records, list) and records:
        rec_url = str(records[0])
    elif isinstance(records, str):
        rec_url = records
    if not rec_url:
        rec_url = item.get("record")

    comment = item.get("communication_comment")
    if isinstance(comment, dict):
        note = _text(comment.get("comment"))
    else:
        note = _text(comment or item.get("note"))

    finish_reason = item.get("call_finish_reason")
    if isinstance(finish_reason, dict):
        missed_status = _text(finish_reason.get("value") or finish_reason.get("value_id"))
    elif finish_reason:
        missed_status = _text(finish_reason)
    else:
        missed = item.get("missedStatus")
        try:
            missed_status = MISSED_STATUS.get(int(missed)) if missed is not None else None
        except (TypeError, ValueError):
            missed_status = None

    return {
        "id": _text(uid),
        "date": _text(start),
        "direction": _text(direction),
        "status": status,
        "client_number": _text(client),
        "diversion": _text(item.get("virtual_phone_number") or item.get("diversion")),
        "telnum_name": _text(item.get("virtual_number_comment") or item.get("telnum_name")),
        "destination": _text(item.get("destination")),
        "operator_login": op_login,
        "operator_name": op_name,
        "group_name": _text(item.get("group_name")),
        "wait": wait_val,
        "duration": dur,
        "record_url": _text(rec_url),
        "rating": _number(item.get("employee_rating") or item.get("rating")),
        "note": note,
        "missed_status": missed_status,
    }


def _operator_row(item: dict) -> dict:
    """Сотрудник из /employee → строка с ключами из COLUMNS["operators"]."""
    first_name = _text(item.get("first_name"))
    last_name = _text(item.get("last_name"))
    full_name = _text(item.get("full_name") or item.get("name"))
    if not full_name:
        parts = [p for p in (last_name, first_name) if p]
        full_name = " ".join(parts) if parts else None

    is_sip = item.get("is_sip_registered")
    if is_sip is True:
        status = "online"
    elif is_sip is False:
        status = "offline"
    else:
        status = _text(item.get("status"))

    redirect = item.get("mobile_redirect")
    if isinstance(redirect, dict):
        enabled = redirect.get("enabled")
        forward = redirect.get("forward")
        delay = redirect.get("delay")
    else:
        enabled = item.get("is_phone_dialing_enabled")
        dialing_type = _text(item.get("phone_dialing_type"))
        forward = 1 if dialing_type == "dialing_rule" else (0 if enabled else None)
        delay = item.get("phone_delay") or item.get("delay")

    return {
        "login": _text(item.get("login")),
        "name": full_name,
        "position": _text(item.get("position_name") or item.get("position")),
        "email": _text(item.get("email")),
        "ext": _text(item.get("short_phone") or item.get("ext")),
        "telnum": _text(item.get("sim_phone") or item.get("telnum")),
        "mobile": _text(item.get("phone") or item.get("mobile")),
        "role": _text(item.get("role_name") or item.get("role")),
        "status": status,
        "mobile_redirect": _flag(enabled),
        "mobile_redirect_forward": _flag(forward) if enabled else None,
        "mobile_redirect_delay": _number(delay) if enabled else None,
    }


def _group_row(item: dict) -> dict:
    """Отдел из /employee-group → строка с ключами из COLUMNS["groups"]."""
    timeout = item.get("timeout")
    timeout = timeout if isinstance(timeout, dict) else {}
    users = item.get("employees") if item.get("employees") is not None else item.get("users")

    timeout_time = item.get("fail_dialing_timeout")
    if timeout_time is None:
        timeout_time = timeout.get("time")

    timeout_target = item.get("fail_dialing_action")
    if timeout_target is None:
        timeout_target = timeout.get("target")

    return {
        "id": _text(item.get("id")),
        "name": _text(item.get("name")),
        "ext": _text(item.get("short_phone") or item.get("ext")),
        "call_order": _text(item.get("distribution_type") or item.get("call_order")),
        "call_duration": _number(item.get("distribution_delay") or item.get("call_duration")),
        "timeout_time": _number(timeout_time),
        "timeout_target": _text(timeout_target),
        "advanced": _text(item.get("advanced")),
        "queue_position": _flag(item.get("queue_position")),
        "users_count": len(users) if isinstance(users, list) else 0,
    }


def _member_rows(item: dict, id_to_login: dict | None = None) -> list[dict]:
    """Отдел из /employee-group → строки «отдел — сотрудник» (COLUMNS["group_members"])."""
    users = item.get("employees") if item.get("employees") is not None else item.get("users")
    if not isinstance(users, list):
        return []
    group_id, group_name = _text(item.get("id")), _text(item.get("name"))

    manager_ids = item.get("manager_ids")
    if isinstance(manager_ids, list):
        managers = {str(m) for m in manager_ids if m is not None}
    elif manager_ids is not None:
        managers = {str(manager_ids)}
    else:
        managers = set()

    rows = []
    for user in users:
        if isinstance(user, (str, int)):
            user = {"employee_id": user}
        if not isinstance(user, dict):
            continue

        emp_id = user.get("employee_id") or user.get("id")
        login = _text(user.get("login"))
        if not login and id_to_login:
            login = _text(id_to_login.get(emp_id) or id_to_login.get(str(emp_id)))
        if not login and emp_id is not None:
            login = str(emp_id)

        is_head = user.get("group_head")
        if is_head is None and emp_id is not None:
            is_head = str(emp_id) in managers

        calls_enable = user.get("is_active")
        if calls_enable is None:
            calls_enable = user.get("calls_enable")

        rows.append({
            "group_id": group_id,
            "group_name": group_name,
            "login": login,
            "calls_enable": _flag(calls_enable),
            "group_head": _flag(is_head),
            "is_absence_active": _flag(user.get("is_absence_active")),
            "is_forwarding_disabled": _flag(user.get("is_forwarding_disabled")),
        })
    return rows


def _text(value):
    """Строка или None; пустые строки Мегафона («"user": ""») — тоже None."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if text.strip() else None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _flag(value):
    """true/false из ответа → 1/0 (у колонок нет логического типа)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().lower() in ("1", "true", "yes", "on")
    return 1 if value else 0


class MegafonConnector(Connector):
    """Телефония Мегафон: звонки, сотрудники и отделы из ВАТС."""

    # --- Карточка в маркетплейсе (операция manifest) ----------------------- #

    name = "megafon-telephony"  # латиница/цифры/«-»/«_»; между версиями не менять
    label = "Телефония Мегафон"
    description = "Звонки, сотрудники и отделы виртуальной АТС Мегафон."
    version = "0.3.0"
    publisher = "ITGRN"
    docs_url = ""

    # --- Форма подключения источника: что вводит пользователь -------------- #

    fields = (
        Field(
            "api_domain",
            "Домен АТС Мегафона",
            required=False,
            default=DEFAULT_DOMAIN,
            placeholder=DEFAULT_DOMAIN,
            help="По умолчанию simple-api.cp.megafon.ru (ВАТС 2.0). "
                 "Для устаревшей ВАТС 1.0 укажите api.megapbx.ru.",
        ),
        Field(
            "api_key",
            "Ключ для авторизации в АТС",
            type="password",
            secret=True,
            help="Личный кабинет Мегафона → Интеграция с CRM → API → «Сгенерировать ключ»",
        ),
    )

    pushdown_operators = frozenset({"gte", "lt"})
    pushdown_fields = frozenset({DATE_COLUMN})

    # --- Наборы данных известны заранее (операция entities) ---------------- #

    entity_catalog = (CALLS, OPERATORS, GROUPS, GROUP_MEMBERS)

    # --- Данные (операция fetch) ------------------------------------------- #

    async def fetch(
        self, ctx: Context, entity: str, conditions: tuple[Condition, ...]
    ) -> FetchResult:
        """Строки сущности из API Мегафона."""
        self.entity(entity)
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        is_v1 = _is_v1(domain)

        if entity == OPERATORS.id:
            path = "/crmapi/v1/users" if is_v1 else USERS_PATH
            params = {"with": "status"} if is_v1 else {}
            items = await self._list(ctx, path, params)
            return FetchResult([_operator_row(i) for i in items])

        if entity == GROUPS.id:
            path = "/crmapi/v1/groups" if is_v1 else GROUPS_PATH
            items = await self._list(ctx, path)
            return FetchResult([_group_row(i) for i in items])

        if entity == GROUP_MEMBERS.id:
            groups_path = "/crmapi/v1/groups" if is_v1 else GROUPS_PATH
            users_path = "/crmapi/v1/users" if is_v1 else USERS_PATH
            items = await self._list(ctx, groups_path)
            try:
                emp_list = await self._list(ctx, users_path)
                id_to_login = {}
                for emp in emp_list:
                    if isinstance(emp, dict) and "id" in emp:
                        id_to_login[emp["id"]] = emp.get("login")
                        id_to_login[str(emp["id"])] = emp.get("login")
            except Exception:
                id_to_login = {}
            return FetchResult([row for i in items for row in _member_rows(i, id_to_login)])

        # entity == CALLS.id
        if is_v1:
            start, end, applied = _period_v1(conditions)
            params = {name: value for name, value in (("start", start), ("end", end)) if value}
            data = await self._get(ctx, HISTORY_PATH, params)
            if not isinstance(data, list):
                raise UpstreamError("АТС Мегафона ответила в неожиданном формате")
            rows = [_call_row(item) for item in data if isinstance(item, dict)]
            return FetchResult(rows, applied=applied)

        # ВАТС 2.0: JSON-RPC метод get.mgf_calls
        date_from, date_till, applied = _period_v2(conditions)
        report_id = await self._get_report_id(ctx)

        # Подготавливаем сопоставление имён сотрудников с логинами
        try:
            emp_list = await self._list(ctx, USERS_PATH)
            name_to_login = {}
            for emp in emp_list:
                if isinstance(emp, dict):
                    login = emp.get("login")
                    full_name = _operator_row(emp).get("name")
                    if full_name and login:
                        name_to_login[full_name] = login
        except Exception:
            name_to_login = {}

        all_rows: list[dict] = []
        offset = 0

        for _ in range(MAX_PAGES):
            rpc_params = {
                "report_id": report_id,
                "date_from": date_from,
                "date_till": date_till,
                "fields": CALL_FIELDS,
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            res = await self._rpc(ctx, "get.mgf_calls", rpc_params)
            items = res.get("data", []) if isinstance(res, dict) else []
            if not isinstance(items, list):
                raise UpstreamError("АТС Мегафона ответила в неожиданном формате")

            page_rows = [_call_row(item, name_to_login) for item in items if isinstance(item, dict)]
            all_rows.extend(page_rows)

            metadata = res.get("metadata", {}) if isinstance(res, dict) else {}
            total_items = _number(metadata.get("total_items"))
            if not items or len(items) < PAGE_LIMIT or (total_items is not None and len(all_rows) >= total_items):
                break
            offset += len(items)

        return FetchResult(all_rows, applied=applied)

    # --- Колонки без выгрузки строк (операция describe) -------------------- #

    async def describe(
        self, ctx: Context, entity_ids: list[str]
    ) -> dict[str, list[Column]]:
        return {i: COLUMNS[i] for i in entity_ids if i in COLUMNS}

    # --- Кнопка «Проверить» (операция test) -------------------------------- #

    async def test(self, ctx: Context) -> str:
        """Лёгкая проверка ключа — список сотрудников вместо истории звонков."""
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        path = "/crmapi/v1/users" if _is_v1(domain) else USERS_PATH
        items = await self._list(ctx, path)
        return f"Подключение к АТС Мегафона работает; сотрудников: {len(items)}"

    # --- Утилиты ------------------------------------------------------------ #

    def entity(self, entity_id: str) -> Entity:
        """Сущность по id; пригодится в fetch."""
        for item in self.entity_catalog:
            if item.id == entity_id:
                return item
        raise ConfigError(f"Нет сущности «{entity_id}»")

    async def _get(self, ctx: Context, path: str, params: dict | None = None):
        """GET к REST API Мегафона → разобранный JSON."""
        api_key = ctx.require("api_key")
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        is_v1 = _is_v1(domain)

        headers = {
            "Content-Type": "application/json; charset=UTF-8",
        }
        if is_v1:
            headers["X-API-KEY"] = api_key
        else:
            headers["Authorization"] = api_key

        response = await egress.fetch(
            "GET",
            f"https://{domain}{path}",
            params=params or {},
            headers=headers,
            timeout=60.0,
            transport=httpx.AsyncHTTPTransport(verify=False),
        )
        if response.status_code in (401, 403):
            raise ConfigError("Мегафон отверг ключ — проверьте «Ключ для авторизации в АТС»")
        if response.status_code >= 400:
            raise UpstreamError(f"АТС Мегафона ответила ошибкой (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError:
            raise UpstreamError("АТС Мегафона ответила не в формате JSON") from None

    async def _rpc(self, ctx: Context, method: str, params: dict | None = None) -> dict:
        """JSON-RPC 2.0 POST запрос к data-api ВАТС 2.0."""
        api_key = ctx.require("api_key")
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        data_domain = _data_api_domain(domain)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        response = await egress.fetch(
            "POST",
            f"https://{data_domain}{RPC_PATH}",
            json=payload,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=60.0,
            transport=httpx.AsyncHTTPTransport(verify=False),
        )
        if response.status_code in (401, 403):
            raise ConfigError("Мегафон отверг ключ — проверьте «Ключ для авторизации в АТС»")
        if response.status_code >= 400:
            raise UpstreamError(f"АТС Мегафона (data-api) ответила ошибкой (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("АТС Мегафона ответила не в формате JSON") from None

        if not isinstance(data, dict):
            raise UpstreamError("АТС Мегафона ответила в неожиданном формате")
        if "error" in data:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise UpstreamError(f"Ошибка API Мегафона ({method}): {msg}")
        return data.get("result", {})

    async def _get_report_id(self, ctx: Context) -> int:
        """Получает report_id для отчёта звонков («mgf_calls» / «История звонков»)."""
        result = await self._rpc(ctx, "get.reports_list", {})
        reports = result.get("data", []) if isinstance(result, dict) else []
        if not isinstance(reports, list) or not reports:
            raise UpstreamError("В личном кабинете Мегафона не найдено ни одного отчёта")

        for r in reports:
            if isinstance(r, dict) and r.get("type") == "mgf_calls":
                return r["id"]
        for r in reports:
            if isinstance(r, dict) and "звонк" in str(r.get("name", "")).lower():
                return r["id"]
        for r in reports:
            if isinstance(r, dict) and r.get("type") == "calls":
                return r["id"]
        first = reports[0]
        if isinstance(first, dict) and "id" in first:
            return first["id"]
        raise UpstreamError("Не удалось определить ID отчёта звонков в Мегафоне")

    async def _list(self, ctx: Context, path: str, params: dict | None = None) -> list[dict]:
        """Справочник целиком.

        Ответ может быть как обычным списком [...], так и постраничным
        {"items": [...], "info": {"start", "limit", "total"}}.
        """
        items: list[dict] = []
        query = dict(params or {})
        for _ in range(MAX_PAGES):
            data = await self._get(ctx, path, query)
            if isinstance(data, list):
                return [i for i in data if isinstance(i, dict)]
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise UpstreamError("АТС Мегафона ответила в неожиданном формате")
            page = [i for i in data["items"] if isinstance(i, dict)]
            items.extend(page)
            info = data.get("info") if isinstance(data.get("info"), dict) else {}
            total = _number(info.get("total"))
            if not page or total is None or len(items) >= total:
                return items
            query["start"] = len(items)
            if _number(info.get("limit")):
                query["limit"] = info["limit"]
        raise UpstreamError("АТС Мегафона отдаёт слишком много страниц")

"""Коннектор Инфопанели для телефонии Мегафон (виртуальная АТС).

Данные берутся из CRM API (документация: https://api.megapbx.ru/#/docs/crmapi/v1):

- звонки — ``GET /crmapi/v1/history/json``;
- сотрудники — ``GET /crmapi/v1/users?with=status``;
- отделы и их состав — ``GET /crmapi/v1/groups``.

Авторизация — заголовок ``X-API-KEY`` с ключом из поля «Ключ для авторизации
в АТС» личного кабинета Мегафона; адрес вида ``https://{домен}/crmapi/v1/…``,
домен вводит пользователь при подключении источника.

Запуск из корня репозитория (PowerShell):

    $env:INFOPANEL_SIGNING_SECRET = "dev"
    infopanel-connect serve megafon_connector:MegafonConnector

Проверка без платформы (secrets.json — {"api_key": "<ключ>"}):

    infopanel-connect check megafon_connector:MegafonConnector --secrets secrets.json

Протокол и шаблоны — docs/protocol.md и docs/templates.md в репозитории
connector-sdk (https://gitlab.itgrn.ru/infopanel/connector-sdk).
"""
from __future__ import annotations

from datetime import datetime, timezone

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

# --------------------------------------------------------------------------- #
# Сущности (таблицы), которые коннектор отдаёт платформе.
# id уникален в пределах источника; подпись видит пользователь.
# --------------------------------------------------------------------------- #

CALLS = Entity("calls", "Звонки")
OPERATORS = Entity("operators", "Сотрудники")
GROUPS = Entity("groups", "Отделы")
GROUP_MEMBERS = Entity("group_members", "Состав отделов")

# --------------------------------------------------------------------------- #
# CRM API Мегафона (https://api.megapbx.ru/#/docs/crmapi/v1): адрес вида
# https://{домен}/crmapi/v1/…, авторизация заголовком X-API-KEY.
# --------------------------------------------------------------------------- #

HISTORY_PATH = "/crmapi/v1/history/json"
USERS_PATH = "/crmapi/v1/users"
GROUPS_PATH = "/crmapi/v1/groups"
DATE_COLUMN = "date"  # колонка, по которой приходит период датасета
DEFAULT_DOMAIN = "api.megapbx.ru"  # домен CRM API, если у клиента АТС не на своём домене
MAX_PAGES = 1000  # предохранитель от бесконечного обхода страниц

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
    # По полям ответа /crmapi/v1/history/json (uid, start, type, status, client,
    # diversion, telnum_name, destination, user, user_name, group_name, wait,
    # duration, record, rating, note, missedStatus).
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
    # По ответу /crmapi/v1/users?with=status; login совпадает с
    # calls.operator_login и group_members.login.
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
    # По ответу /crmapi/v1/groups; объект timeout развёрнут в две колонки.
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
    # Строка на пару «отдел — сотрудник» из groups[].users[]: сотрудник может
    # состоять в нескольких отделах.
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
# Помощники: домен, даты периода, разбор ответа (как load() в примере SDK).
# --------------------------------------------------------------------------- #

def _domain(raw: str) -> str:
    """Домен из формы: терпим «https://ats.x.ru/», оставляем «ats.x.ru»."""
    text = str(raw or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].strip()
    if not text or "@" in text or ":" in text:
        raise ConfigError("«Домен АТС» — имя сервера, например ats.company.ru")
    return text


def _megafon_dt(value) -> str | None:
    """«2026-01-31T00:00:00+03:00» → «20260130T210000Z» (UTC, формат Мегафона)."""
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


def _period(conditions: tuple[Condition, ...]):
    """Период датасета (gte начала, lt конца по колонке date) → start/end."""
    start = end = None
    applied: list[Condition] = []
    for cond in conditions:
        if cond.field != DATE_COLUMN or cond.op not in ("gte", "lt"):
            continue
        value = _megafon_dt(cond.value)
        if not value:
            continue
        if cond.op == "gte" and start is None:
            start = value
            applied.append(cond)
        elif cond.op == "lt" and end is None:
            end = value
            applied.append(cond)
    return start, end, tuple(applied)


def _call_row(item: dict) -> dict:
    """Звонок из /history/json → строка с ключами из COLUMNS["calls"]."""
    missed = item.get("missedStatus")
    try:
        missed_status = MISSED_STATUS.get(int(missed)) if missed is not None else None
    except (TypeError, ValueError):
        missed_status = None
    return {
        "id": _text(item.get("uid")),
        "date": _text(item.get("start")),
        "direction": _text(item.get("type")),
        "status": _text(item.get("status")),
        "client_number": _text(item.get("client")),
        "diversion": _text(item.get("diversion")),
        "telnum_name": _text(item.get("telnum_name")),
        "destination": _text(item.get("destination")),
        "operator_login": _text(item.get("user")),
        "operator_name": _text(item.get("user_name")),
        "group_name": _text(item.get("group_name")),
        "wait": _number(item.get("wait")),
        "duration": _number(item.get("duration")),
        "record_url": _text(item.get("record")),
        "rating": _number(item.get("rating")),
        "note": _text(item.get("note")),
        "missed_status": missed_status,
    }


def _operator_row(item: dict) -> dict:
    """Сотрудник из /users → строка с ключами из COLUMNS["operators"].

    mobile_redirect приходит как {"enabled": true, "forward": false, "delay": 15};
    при выключенной переадресации — только {"enabled": false}.
    """
    redirect = item.get("mobile_redirect")
    redirect = redirect if isinstance(redirect, dict) else {}
    enabled = redirect.get("enabled")
    return {
        "login": _text(item.get("login")),
        "name": _text(item.get("name")),
        "position": _text(item.get("position")),
        "email": _text(item.get("email")),
        "ext": _text(item.get("ext")),
        "telnum": _text(item.get("telnum")),
        "mobile": _text(item.get("mobile")),
        "role": _text(item.get("role")),
        "status": _text(item.get("status")),
        "mobile_redirect": _flag(enabled),
        "mobile_redirect_forward": _flag(redirect.get("forward")) if enabled else None,
        "mobile_redirect_delay": _number(redirect.get("delay")) if enabled else None,
    }


def _group_row(item: dict) -> dict:
    """Отдел из /groups → строка с ключами из COLUMNS["groups"]."""
    timeout = item.get("timeout")
    timeout = timeout if isinstance(timeout, dict) else {}
    users = item.get("users")
    return {
        "id": _text(item.get("id")),
        "name": _text(item.get("name")),
        "ext": _text(item.get("ext")),
        "call_order": _text(item.get("call_order")),
        "call_duration": _number(item.get("call_duration")),
        "timeout_time": _number(timeout.get("time")),
        "timeout_target": _text(timeout.get("target")),
        "advanced": _text(item.get("advanced")),
        "queue_position": _flag(item.get("queue_position")),
        "users_count": len(users) if isinstance(users, list) else 0,
    }


def _member_rows(item: dict) -> list[dict]:
    """Отдел из /groups → строки «отдел — сотрудник» (COLUMNS["group_members"])."""
    users = item.get("users")
    if not isinstance(users, list):
        return []
    group_id, group_name = _text(item.get("id")), _text(item.get("name"))
    rows = []
    for user in users:
        if isinstance(user, str):  # на случай ответа списком логинов
            user = {"login": user}
        if not isinstance(user, dict):
            continue
        rows.append({
            "group_id": group_id,
            "group_name": group_name,
            "login": _text(user.get("login")),
            "calls_enable": _flag(user.get("calls_enable")),
            "group_head": _flag(user.get("group_head")),
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
    """Телефония Мегафон: звонки, сотрудники и отделы из CRM API."""

    # --- Карточка в маркетплейсе (операция manifest) ----------------------- #

    name = "megafon-telephony"  # латиница/цифры/«-»/«_»; между версиями не менять
    label = "Телефония Мегафон"
    description = "Звонки, сотрудники и отделы виртуальной АТС Мегафон."
    version = "0.3.0"
    publisher = "ITGRN"
    docs_url = ""  # ссылка на документацию, если появится

    # --- Форма подключения источника: что вводит пользователь -------------- #
    # secret-поля хранятся на платформе зашифрованными и приходят в ctx.secrets.

    fields = (
        Field(
            "api_domain",
            "Домен АТС Мегафона",
            required=False,
            default=DEFAULT_DOMAIN,
            placeholder=DEFAULT_DOMAIN,
            help="Обычно api.megapbx.ru — оставьте как есть, если ваша АТС "
                 "не живёт на отдельном домене; подставляется в "
                 "https://<домен>/crmapi/v1/…",
        ),
        Field(
            "api_key",
            "Ключ для авторизации в АТС",
            type="password",
            secret=True,
            help="Личный кабинет Мегафона → настройки интеграции → "
                 "«Ключ для авторизации в АТС»",
        ),
    )

    # Период датасета («этот месяц», «последние 30 дней») приходит двумя
    # условиями gte/lt по колонке date — передаём их в API как start/end.
    # Это подсказка конструктору датасетов; фактически применённые условия
    # возвращаются из fetch в FetchResult.applied.
    pushdown_operators = frozenset({"gte", "lt"})
    pushdown_fields = frozenset({DATE_COLUMN})

    # --- Наборы данных известны заранее (операция entities) ---------------- #
    # entities() не переопределяем: базовый отдаст entity_catalog как есть.

    entity_catalog = (CALLS, OPERATORS, GROUPS, GROUP_MEMBERS)

    # --- Данные (операция fetch) ------------------------------------------- #

    async def fetch(
        self, ctx: Context, entity: str, conditions: tuple[Condition, ...]
    ) -> FetchResult:
        """Строки сущности из CRM API.

        Все методы отдают данные целиком (история за период — одним ответом,
        справочники обходим по страницам сами), поэтому fetch_page не
        переопределяем: SDK сам нарежет строки на страницы.
        """
        self.entity(entity)

        if entity == OPERATORS.id:
            items = await self._list(ctx, USERS_PATH, {"with": "status"})
            return FetchResult([_operator_row(i) for i in items])
        if entity == GROUPS.id:
            items = await self._list(ctx, GROUPS_PATH)
            return FetchResult([_group_row(i) for i in items])
        if entity == GROUP_MEMBERS.id:
            items = await self._list(ctx, GROUPS_PATH)
            return FetchResult([row for i in items for row in _member_rows(i)])

        start, end, applied = _period(conditions)
        params = {name: value for name, value in (("start", start), ("end", end)) if value}
        data = await self._get(ctx, HISTORY_PATH, params)
        if not isinstance(data, list):
            raise UpstreamError("АТС Мегафона ответила в неожиданном формате")
        rows = [_call_row(item) for item in data if isinstance(item, dict)]
        return FetchResult(rows, applied=applied)

    # --- Колонки без выгрузки строк (операция describe) -------------------- #

    async def describe(
        self, ctx: Context, entity_ids: list[str]
    ) -> dict[str, list[Column]]:
        return {i: COLUMNS[i] for i in entity_ids if i in COLUMNS}

    # --- Кнопка «Проверить» (операция test) -------------------------------- #

    async def test(self, ctx: Context) -> str:
        """Лёгкая проверка ключа — список сотрудников вместо истории звонков."""
        items = await self._list(ctx, USERS_PATH)
        return f"Подключение к АТС Мегафона работает; сотрудников: {len(items)}"

    # --- Утилиты ------------------------------------------------------------ #

    def entity(self, entity_id: str) -> Entity:
        """Сущность по id; пригодится в fetch."""
        for item in self.entity_catalog:
            if item.id == entity_id:
                return item
        raise ConfigError(f"Нет сущности «{entity_id}»")

    async def _get(self, ctx: Context, path: str, params: dict | None = None):
        """GET к CRM API → разобранный JSON; ошибки — понятными исключениями SDK."""
        api_key = ctx.require("api_key")
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        # egress.fetch проверяет адрес (домен вводит пользователь — защита от
        # походов во внутреннюю сеть), ходит через корпоративный прокси, если
        # он задан, и сам оборачивает таймауты и обрывы связи в UpstreamError.
        response = await egress.fetch(
            "GET",
            f"https://{domain}{path}",
            params=params or {},
            headers={"X-API-KEY": api_key},
            timeout=60.0,
        )
        if response.status_code in (401, 403):
            raise ConfigError("Мегафон отверг ключ — проверьте «Ключ для авторизации в АТС»")
        if response.status_code >= 400:
            raise UpstreamError(f"АТС Мегафона ответила ошибкой (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError:
            raise UpstreamError("АТС Мегафона ответила не в формате JSON") from None

    async def _list(self, ctx: Context, path: str, params: dict | None = None) -> list[dict]:
        """Справочник целиком.

        Ответ — {"items": [...], "info": {"start", "limit", "total"}}: пока не
        набрали total, просим следующую страницу с start. Голый список тоже
        принимаем.
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

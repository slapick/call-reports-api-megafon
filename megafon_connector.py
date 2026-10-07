"""Коннектор Инфопанели для телефонии Мегафон (виртуальная АТС).

Звонки выгружаются методом CRM API ``GET /crmapi/v1/history/json``
(документация: https://api.megapbx.ru/#/docs/crmapi/v1). Авторизация —
заголовок ``X-API-KEY`` с ключом из поля «Ключ для авторизации в АТС» личного
кабинета Мегафона; адрес вида ``https://{домен}/crmapi/v1/…``, домен вводит
пользователь при подключении источника.

Сотрудники (``operators``) пока не выгружаются: нужен метод
``GET /crmapi/v1/users`` и его схема ответа — см. заглушку в ``fetch``.

Запуск из корня репозитория (PowerShell):

    $env:INFOPANEL_SIGNING_SECRET = "dev"
    infopanel-connect serve megafon_connector:MegafonConnector

Проверка без платформы:

    infopanel-connect check megafon_connector:MegafonConnector

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

# --------------------------------------------------------------------------- #
# CRM API Мегафона (https://api.megapbx.ru/#/docs/crmapi/v1): адрес вида
# https://{домен}/crmapi/v1/…, авторизация заголовком X-API-KEY.
# --------------------------------------------------------------------------- #

HISTORY_PATH = "/crmapi/v1/history/json"
USERS_PATH = "/crmapi/v1/users"  # для сущности «operators» — ждём схему ответа
DATE_COLUMN = "date"  # колонка, по которой приходит период датасета
DEFAULT_DOMAIN = "api.megapbx.ru"  # домен CRM API, если у клиента АТС не на своём домене

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
# Ключи строк из fetch обязаны совпадать с этими именами; состав — по полям
# ответа /crmapi/v1/history/json (uid, start, type, status, client, diversion,
# telnum_name, destination, user, user_name, group_name, wait, duration,
# record, rating, note, missedStatus).
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
    # TODO: выровнять по ответу GET /crmapi/v1/users, когда будет схема.
    "operators": [
        Column("id", "ID сотрудника", "string"),
        Column("name", "Имя", "string"),
        Column("extension", "Внутренний номер", "string"),
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
    """Ответ Мегафона → строка с ключами из COLUMNS["calls"]."""
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


def _text(value):
    return None if value is None else (value if isinstance(value, str) else str(value))


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


class MegafonConnector(Connector):
    """Телефония Мегафон: звонки выгружаются из CRM API; сотрудники — в работе."""

    # --- Карточка в маркетплейсе (операция manifest) ----------------------- #

    name = "megafon-telephony"  # латиница/цифры/«-»/«_»; между версиями не менять
    label = "Телефония Мегафон"
    description = "Звонки и сотрудники виртуальной АТС Мегафон."
    version = "0.2.0"
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

    entity_catalog = (CALLS, OPERATORS)

    # --- Данные (операция fetch) ------------------------------------------- #

    async def fetch(
        self, ctx: Context, entity: str, conditions: tuple[Condition, ...]
    ) -> FetchResult:
        """Строки сущности: звонки — из CRM API, сотрудники — заглушка.

        Метод отдаёт всю историю за период одним ответом (пагинации в
        /crmapi/v1/history/json нет — есть только limit), поэтому fetch_page
        не переопределяем: SDK сам нарежет строки на страницы.
        """
        self.entity(entity)
        if entity == OPERATORS.id:
            raise NotImplementedError(
                "Сотрудники: реализуйте GET /crmapi/v1/users, когда будет "
                "известна схема ответа (см. TODO у COLUMNS[\"operators\"])"
            )

        api_key = ctx.require("api_key")
        domain = _domain(ctx.value("api_domain") or DEFAULT_DOMAIN)
        start, end, applied = _period(conditions)

        # egress.fetch проверяет адрес (домен вводит пользователь — защита от
        # походов во внутреннюю сеть), ходит через корпоративный прокси, если
        # он задан, и сам оборачивает таймауты и обрывы связи в UpstreamError.
        response = await egress.fetch(
            "GET",
            f"https://{domain}{HISTORY_PATH}",
            params={name: value for name, value in (("start", start), ("end", end)) if value},
            headers={"X-API-KEY": api_key},
            timeout=60.0,
        )
        if response.status_code in (401, 403):
            raise ConfigError("Мегафон отверг ключ — проверьте «Ключ для авторизации в АТС»")
        if response.status_code >= 400:
            raise UpstreamError(f"АТС Мегафона ответила ошибкой (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("АТС Мегафона ответила не в формате JSON") from None
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
    # Не переопределяем: базовый test проверит сущности и первую выгрузку.
    # Когда появится дешёвый endpoint (профиль/баланс) — замените на него.

    # --- Утилиты ------------------------------------------------------------ #

    def entity(self, entity_id: str) -> Entity:
        """Сущность по id; пригодится в fetch."""
        for item in self.entity_catalog:
            if item.id == entity_id:
                return item
        raise ConfigError(f"Нет сущности «{entity_id}»")

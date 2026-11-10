"""Готовые датасеты, виджеты и дашборды коннектора Мегафон.

Пользователь выбирает их в галерее Инфопанели и сразу видит свои данные; при
установке из маркетплейса быстрый старт создаёт «Пульс дня» и «Итоги месяца».
Формат — docs/templates.md в репозитории connector-sdk.

id шаблонов, веток и показателей между версиями не менять: по ним платформа
узнаёт уже созданные датасеты.
"""
from __future__ import annotations

from infopanel_connect import (
    Branch,
    DashboardTemplate,
    DatasetTemplate,
    Join,
    Output,
    Place,
    QuickStart,
    WidgetTemplate,
)

# --------------------------------------------------------------------------- #
# Колонки звонков (см. COLUMNS в megafon_connector.py). Псевдоним базы — calls.
# --------------------------------------------------------------------------- #

CALL_AT = "calls.date"
WHO = "calls.operator_name"
DEPARTMENT = "calls.group_name"
MINUTES = "calls.minutes"
CLIENT = "calls.client_phone"
LINE = "calls.diversion"
HOUR = "calls.hour"
WEEKDAY = "calls.weekday"
STATUS = "calls.status_name"
DIRECTION = "calls.direction_name"
ANSWERED = "calls.is_answered"
MISSED = "calls.is_missed"
INBOUND = "calls.is_inbound"
# Для долей — флаги 100/0: формат percent только дописывает «%», не умножает.
ANSWERED_PERCENT = "calls.answered_percent"
MISSED_PERCENT = "calls.missed_percent"

# --------------------------------------------------------------------------- #
# Повторяющиеся шаги. Период и условия в начале цепочки платформа передаёт
# коннектору: период уходит в API как date_from/date_till, остальное досчитает платформа.
# --------------------------------------------------------------------------- #


def period(preset: str) -> dict:
    return {"id": "period", "type": "period", "field": CALL_AT, "preset": preset}


def where(step_id: str, *conditions: tuple) -> dict:
    """Условия через И: (поле, оператор, значение); у empty/not_empty значение — None."""
    return {"id": step_id, "type": "filter", "conditions": [
        {"field": f, "op": op, **({} if v is None else {"value": v})} for f, op, v in conditions
    ]}


# Внутренние звонки (сотрудник — сотруднику) в отчёты о клиентах не идут.
EXTERNAL = where("external", ("calls.is_internal", "ne", True))
ONLY_INBOUND = ("calls.direction", "eq", "in")
ONLY_OUTBOUND = ("calls.direction", "eq", "out")


def count(key: str) -> dict:
    return {"key": key, "fn": "count"}


def total(key: str, field: str) -> dict:
    return {"key": key, "fn": "sum", "field": field}


def summary(by: dict[str, str], *aggregates: dict, rank: str) -> tuple[dict, ...]:
    """Сводка по колонкам ``by`` (колонка → подпись), по убыванию ``rank``."""
    return (
        {"id": "summary", "type": "group", "by": list(by), "aggregates": list(aggregates)},
        {"id": "named", "type": "fields",
         "columns": [{"key": k, "label": v} for k, v in by.items()] + [{"key": a["key"]} for a in aggregates]},
        {"id": "ranked", "type": "sort", "rules": [{"field": rank, "dir": "desc"}]},
    )


# Сводка по сотрудникам: одинаковая в дневных и месячных датасетах.
EMPLOYEE_SUMMARY = summary(
    {WHO: "Сотрудник"},
    count("Звонков"), total("Разговоров", ANSWERED), total("Пропущено", MISSED), total("Минут", MINUTES),
    rank="Звонков",
)


def kpis(period_name: str) -> tuple[Output, ...]:
    """Звонков, разговоров, пропущено, минут — над основной цепочкой датасета."""
    return (
        Output("calls", f"Звонков {period_name}", "number", format="integer"),
        Output("answered", f"Разговоров {period_name}", "number", value=ANSWERED, format="integer"),
        Output("missed", f"Пропущено {period_name}", "number", value=MISSED, format="integer"),
        Output("minutes", f"Минут разговоров {period_name}", "number", value=MINUTES, format="integer"),
    )


def by_hour(name: str = "Звонки по часам", **kw) -> Output:
    return Output("by_hour", name, "breakdown", by=HOUR, sort="label", **kw)


# --------------------------------------------------------------------------- #
# Датасеты. Каждый — одна выгрузка звонков за период; ветки и показатели
# считаются платформой поверх неё.
# --------------------------------------------------------------------------- #

CALLS_TODAY = DatasetTemplate(
    "calls_today", "Звонки сегодня", entity="calls",
    description="Звонки за сегодня: сколько, с каким результатом, по часам и по сотрудникам.",
    steps=(period("today"), EXTERNAL),
    branches=(
        Branch("by_employee", "По сотрудникам", after="external", steps=EMPLOYEE_SUMMARY),
        Branch("inbound", "Входящие", after="external", steps=(where("only_inbound", ONLY_INBOUND),)),
    ),
    outputs=(
        *kpis("сегодня"),
        Output("missed_share", "Доля пропущенных входящих сегодня", "number", value=MISSED_PERCENT, fn="avg",
               branch="inbound", format="percent"),
        by_hour(),
        Output("by_type", "Входящие и исходящие сегодня", "breakdown", by=DIRECTION),
        Output("by_employee", "Звонки сегодня по сотрудникам", "breakdown", by=WHO),
        Output("employees", "Сотрудники сегодня", "table", branch="by_employee", limit=50),
    ),
)

CALLBACK_TODAY = DatasetTemplate(
    "callback_today", "Перезвонить сегодня", entity="calls",
    description="Номера, которые сегодня не дозвонились и с которыми ещё не поговорили. "
                "Номер пропадает из списка, как только с ним состоялся разговор — входящий или исходящий.",
    steps=(
        period("today"), EXTERNAL,
        where("with_client", (CLIENT, "not_empty", None)),
        {"id": "by_client", "type": "group", "by": [CLIENT], "aggregates": [
            total("Пропущено", MISSED), total("Разговоров", ANSWERED),
            {"key": "Последний звонок", "fn": "max", "field": CALL_AT},
        ]},
        where("waiting", ("Пропущено", "gt", 0), ("Разговоров", "eq", 0)),
        {"id": "named", "type": "fields", "columns": [
            {"key": CLIENT, "label": "Номер"}, {"key": "Пропущено"},
            {"key": "Последний звонок", "date_format": "datetime"},
        ]},
        {"id": "latest", "type": "sort", "rules": [{"field": "Последний звонок", "dir": "desc"}]},
    ),
    branches=(
        Branch("missed_calls", "Пропущенные звонки", after="external", steps=(
            where("only_missed", (MISSED, "eq", True)),
            {"id": "named", "type": "fields", "columns": [
                {"key": CALL_AT, "label": "Время", "date_format": "datetime"},
                {"key": CLIENT, "label": "Номер"}, {"key": LINE, "label": "Линия"},
                {"key": WHO, "label": "Сотрудник"}, {"key": STATUS, "label": "Статус"},
            ]},
            {"id": "latest", "type": "sort", "rules": [{"field": "Время", "dir": "desc"}]},
        )),
    ),
    outputs=(
        Output("waiting", "Ждут звонка", "number", format="integer"),
        Output("numbers", "Кому перезвонить", "table", limit=100),
        Output("missed_calls", "Пропущенные звонки сегодня", "table", branch="missed_calls", limit=100),
    ),
)

OUTBOUND_TODAY = DatasetTemplate(
    "outbound_today", "Обзвон сегодня", entity="calls",
    description="Исходящие за сегодня: попытки, дозвоны, минуты — по сотрудникам и по часам.",
    steps=(period("today"), EXTERNAL, where("only_outbound", ONLY_OUTBOUND)),
    branches=(
        Branch("by_employee", "По сотрудникам", after="only_outbound", steps=summary(
            {WHO: "Сотрудник"}, count("Попыток"), total("Дозвонов", ANSWERED), total("Минут", MINUTES),
            rank="Попыток",
        )),
    ),
    outputs=(
        Output("attempts", "Исходящих сегодня", "number", format="integer"),
        Output("reached", "Дозвонов сегодня", "number", value=ANSWERED, format="integer"),
        Output("reach_share", "Доля дозвона сегодня", "number", value=ANSWERED_PERCENT, fn="avg", format="percent"),
        Output("minutes", "Минут исходящих сегодня", "number", value=MINUTES, format="integer"),
        Output("by_employee", "Исходящие сегодня по сотрудникам", "breakdown", by=WHO),
        Output("reach_by_employee", "Доля дозвона по сотрудникам", "breakdown", value=ANSWERED_PERCENT, fn="avg",
               by=WHO, format="percent"),
        by_hour("Исходящие по часам"),
        Output("employees", "Обзвон сегодня: сотрудники", "table", branch="by_employee", limit=50),
    ),
)

CALLS_WEEK = DatasetTemplate(
    "calls_week", "Звонки за 7 дней", entity="calls",
    description="Последние 7 дней по дням: звонки, разговоры, пропущенные, минуты.",
    steps=(period("last_7_days"), EXTERNAL),
    outputs=(
        *kpis("за 7 дней"),
        Output("by_day", "Звонки по дням", "series", by=CALL_AT),
        Output("missed_by_day", "Пропущенные по дням", "series", value=MISSED, by=CALL_AT),
        Output("minutes_by_day", "Минуты по дням", "series", value=MINUTES, by=CALL_AT),
        Output("by_type", "Входящие и исходящие", "breakdown", by=DIRECTION),
        Output("by_employee", "Звонки по сотрудникам", "breakdown", by=WHO),
    ),
)

CALLS_MONTH = DatasetTemplate(
    "calls_month", "Звонки за месяц", entity="calls",
    description="Звонки текущего месяца: сколько, кто, с каким результатом, по дням; "
                "лидеры и пропущенные — в ветках.",
    steps=(period("this_month"), EXTERNAL),
    branches=(
        Branch("by_employee", "По сотрудникам", after="external", steps=EMPLOYEE_SUMMARY),
        Branch("top5", "Пятёрка лидеров", parent="by_employee", after="ranked",
               steps=({"id": "five", "type": "limit", "value": 5},)),
        Branch("inbound", "Входящие", after="external", steps=(where("only_inbound", ONLY_INBOUND),)),
        Branch("missed", "Пропущенные", after="external", steps=(where("only_missed", (MISSED, "eq", True)),)),
        Branch("missed_by_employee", "Кто пропускает", parent="missed", after="only_missed", steps=summary(
            {WHO: "Сотрудник"}, count("Пропущено"), rank="Пропущено",
        )),
    ),
    outputs=(
        *kpis("за месяц"),
        Output("missed_share", "Доля пропущенных входящих", "number", value=MISSED_PERCENT, fn="avg",
               branch="inbound", format="percent"),
        Output("by_day", "Звонки по дням", "series", by=CALL_AT),
        Output("minutes_by_day", "Минуты по дням", "series", value=MINUTES, by=CALL_AT),
        Output("by_employee", "Звонки по сотрудникам", "breakdown", by=WHO),
        Output("minutes_by_employee", "Минуты по сотрудникам", "breakdown", value=MINUTES, by=WHO),
        Output("by_result", "Чем закончились звонки", "breakdown", by=STATUS),
        Output("by_type", "Входящие и исходящие", "breakdown", by=DIRECTION),
        Output("employees", "Сотрудники: звонки и минуты", "table", branch="by_employee", limit=20),
        Output("top5", "Пятёрка лидеров по звонкам", "breakdown", value="Звонков", fn="sum",
               by="Сотрудник", branch="top5"),
        Output("missed_by_day", "Пропущенные по дням", "series", by=CALL_AT, branch="missed"),
        Output("missed_by_employee", "Кто пропускает звонки", "breakdown", value="Пропущено", fn="sum",
               by="Сотрудник", branch="missed_by_employee"),
    ),
)

CALLS_LAST_MONTH = DatasetTemplate(
    "calls_last_month", "Звонки за прошлый месяц", entity="calls",
    description="Итоги прошлого месяца — закрытый период для отчётов и сравнения с текущим.",
    steps=(period("last_month"), EXTERNAL),
    branches=(
        Branch("by_employee", "По сотрудникам", after="external", steps=EMPLOYEE_SUMMARY),
        Branch("inbound", "Входящие", after="external", steps=(where("only_inbound", ONLY_INBOUND),)),
    ),
    outputs=(
        *kpis("за прошлый месяц"),
        Output("missed_share", "Доля пропущенных входящих за прошлый месяц", "number", value=MISSED_PERCENT, fn="avg",
               branch="inbound", format="percent"),
        Output("by_day", "Звонки по дням", "series", by=CALL_AT),
        Output("by_type", "Входящие и исходящие", "breakdown", by=DIRECTION),
        Output("by_employee", "Звонки по сотрудникам", "breakdown", by=WHO),
        Output("employees", "Сотрудники за прошлый месяц", "table", branch="by_employee", limit=50),
    ),
)

INBOUND_MONTH = DatasetTemplate(
    "inbound_month", "Входящие за месяц", entity="calls",
    description="Входящие текущего месяца: принято и пропущено, доля пропущенных по дням, "
                "результаты и линии.",
    steps=(period("this_month"), EXTERNAL, where("only_inbound", ONLY_INBOUND)),
    outputs=(
        Output("calls", "Входящих за месяц", "number", format="integer"),
        Output("answered", "Принято за месяц", "number", value=ANSWERED, format="integer"),
        Output("missed", "Пропущено за месяц", "number", value=MISSED, format="integer"),
        Output("missed_share", "Доля пропущенных", "number", value=MISSED_PERCENT, fn="avg", format="percent"),
        Output("minutes", "Минут входящих", "number", value=MINUTES, format="integer"),
        Output("by_day", "Входящие по дням", "series", by=CALL_AT),
        Output("missed_by_day", "Пропущенные по дням", "series", value=MISSED, by=CALL_AT),
        Output("missed_share_by_day", "Доля пропущенных по дням", "series", value=MISSED_PERCENT, fn="avg",
               by=CALL_AT, format="percent"),
        Output("by_status", "Чем закончились входящие", "breakdown", by=STATUS),
        Output("by_line", "Входящие по линиям", "breakdown", by=LINE),
        by_hour("Входящие по часам"),
    ),
)

MISSED_MONTH = DatasetTemplate(
    "missed_month", "Пропущенные за месяц", entity="calls",
    description="Когда и где теряются звонки: по дням, часам, дням недели, линиям и сотрудникам; "
                "полный список — в ветке.",
    steps=(period("this_month"), EXTERNAL, where("only_missed", (MISSED, "eq", True))),
    branches=(
        Branch("list", "Список", after="only_missed", steps=(
            {"id": "named", "type": "fields", "columns": [
                {"key": CALL_AT, "label": "Время", "date_format": "datetime"},
                {"key": CLIENT, "label": "Номер"}, {"key": LINE, "label": "Линия"},
                {"key": WHO, "label": "Сотрудник"}, {"key": STATUS, "label": "Статус"},
            ]},
            {"id": "latest", "type": "sort", "rules": [{"field": "Время", "dir": "desc"}]},
        )),
    ),
    outputs=(
        Output("missed", "Пропущено за месяц", "number", format="integer"),
        Output("clients", "Номеров с пропущенными", "number", value=CLIENT, fn="count_distinct",
               format="integer"),
        Output("by_day", "Пропущенные по дням", "series", by=CALL_AT),
        by_hour("Пропущенные по часам"),
        Output("by_weekday", "Пропущенные по дням недели", "breakdown", by=WEEKDAY, sort="label"),
        Output("by_status", "Кто пропустил: сотрудник или сервис", "breakdown", by=STATUS),
        Output("by_line", "Пропущенные по линиям", "breakdown", by=LINE),
        Output("by_employee", "Пропущенные по сотрудникам", "breakdown", by=WHO),
        Output("list", "Пропущенные звонки", "table", branch="list", limit=200),
    ),
)

LINES_MONTH = DatasetTemplate(
    "lines_month", "Многоканальные номера за месяц", entity="calls",
    description="Входящие по многоканальным номерам: звонки, клиенты, пропущенные. "
                "Если номера заведены под рекламные каналы — это отчёт по рекламе.",
    steps=(period("this_month"), EXTERNAL, where("only_inbound", ONLY_INBOUND, (LINE, "not_empty", None))),
    branches=(
        Branch("by_line", "По номерам", after="only_inbound", steps=summary(
            {LINE: "Номер"},
            count("Звонков"), {"key": "Клиентов", "fn": "count_distinct", "field": CLIENT},
            total("Принято", ANSWERED), total("Пропущено", MISSED), total("Минут", MINUTES),
            rank="Звонков",
        )),
    ),
    outputs=(
        Output("by_line", "Звонки по номерам", "breakdown", by=LINE),
        Output("clients_by_line", "Клиенты по номерам", "breakdown", value=CLIENT, fn="count_distinct", by=LINE),
        Output("missed_by_line", "Пропущенные по номерам", "breakdown", value=MISSED, by=LINE),
        Output("missed_share_by_line", "Доля пропущенных по номерам", "breakdown", value=MISSED_PERCENT, fn="avg",
               by=LINE, format="percent"),
        Output("lines", "Многоканальные номера", "table", branch="by_line", limit=50),
    ),
)

LOAD_30_DAYS = DatasetTemplate(
    "load_30_days", "Нагрузка по часам и дням недели", entity="calls",
    description="Когда звонят чаще всего — за последние 30 дней: по часам, дням недели и их сочетаниям. "
                "Для планирования смен.",
    steps=(period("last_30_days"), EXTERNAL),
    branches=(
        Branch("grid", "День недели × час", after="external", steps=(
            {"id": "summary", "type": "group", "by": [WEEKDAY, HOUR], "aggregates": [
                count("Звонков"), total("Входящих", INBOUND), total("Пропущено", MISSED),
            ]},
            {"id": "named", "type": "fields", "columns": [
                {"key": WEEKDAY, "label": "День недели"}, {"key": HOUR, "label": "Час"},
                {"key": "Звонков"}, {"key": "Входящих"}, {"key": "Пропущено"},
            ]},
            {"id": "ordered", "type": "sort", "rules": [{"field": "День недели", "dir": "asc"},
                                                         {"field": "Час", "dir": "asc"}]},
        )),
    ),
    outputs=(
        by_hour(),
        Output("inbound_by_hour", "Входящие по часам", "breakdown", value=INBOUND, by=HOUR, sort="label"),
        Output("missed_by_hour", "Пропущенные по часам", "breakdown", value=MISSED, by=HOUR, sort="label"),
        Output("by_weekday", "Звонки по дням недели", "breakdown", by=WEEKDAY, sort="label"),
        Output("missed_by_weekday", "Пропущенные по дням недели", "breakdown", value=MISSED, by=WEEKDAY,
               sort="label"),
        Output("grid", "Нагрузка: день недели × час", "table", branch="grid", limit=168),
    ),
)

OUTBOUND_MONTH = DatasetTemplate(
    "outbound_month", "Исходящие за месяц", entity="calls",
    description="Исходящие текущего месяца: попытки, дозвоны, доля дозвона и средний разговор — "
                "по дням и по сотрудникам; короткие разговоры (до 10 секунд) — отдельно.",
    steps=(period("this_month"), EXTERNAL, where("only_outbound", ONLY_OUTBOUND)),
    branches=(
        Branch("by_employee", "По сотрудникам", after="only_outbound", steps=summary(
            {WHO: "Сотрудник"}, count("Попыток"), total("Дозвонов", ANSWERED), total("Минут", MINUTES),
            rank="Попыток",
        )),
        Branch("reached", "Дозвоны", after="only_outbound", steps=(where("only_reached", (ANSWERED, "eq", True)),)),
        Branch("short", "Короткие разговоры", parent="reached", after="only_reached",
               steps=(where("under_10s", ("calls.duration", "lt", 10)),)),
    ),
    outputs=(
        Output("attempts", "Исходящих за месяц", "number", format="integer"),
        Output("reached", "Дозвонов за месяц", "number", value=ANSWERED, format="integer"),
        Output("reach_share", "Доля дозвона", "number", value=ANSWERED_PERCENT, fn="avg", format="percent"),
        Output("by_result", "Дозвонились или нет", "breakdown", by=STATUS),
        Output("avg_talk", "Средний разговор, мин", "number", value=MINUTES, fn="avg", branch="reached",
               format="decimal"),
        Output("minutes", "Минут исходящих", "number", value=MINUTES, format="integer"),
        Output("by_day", "Исходящие по дням", "series", by=CALL_AT),
        Output("reached_by_day", "Дозвоны по дням", "series", value=ANSWERED, by=CALL_AT),
        Output("by_employee", "Исходящие по сотрудникам", "breakdown", by=WHO),
        Output("reach_by_employee", "Доля дозвона по сотрудникам", "breakdown", value=ANSWERED_PERCENT, fn="avg",
               by=WHO, format="percent"),
        Output("employees", "Обзвон: сотрудники", "table", branch="by_employee", limit=50),
        Output("short", "Разговоров короче 10 секунд", "number", branch="short", format="integer"),
        Output("short_by_employee", "Короткие разговоры по сотрудникам", "breakdown", by=WHO, branch="short"),
    ),
)

EMPLOYEES_MONTH = DatasetTemplate(
    "employees_month", "Сотрудники за месяц", entity="calls",
    description="Рейтинг сотрудников за текущий месяц: звонки, входящие, разговоры, пропущенные, "
                "минуты и клиенты; доля пропущенных и средний разговор.",
    steps=(period("this_month"), EXTERNAL, where("with_employee", (WHO, "not_empty", None))),
    branches=(
        Branch("rating", "Рейтинг", after="with_employee", steps=summary(
            {WHO: "Сотрудник", DEPARTMENT: "Отдел"},
            count("Звонков"), total("Входящих", INBOUND), total("Разговоров", ANSWERED),
            total("Пропущено", MISSED), total("Минут", MINUTES),
            {"key": "Клиентов", "fn": "count_distinct", "field": CLIENT},
            rank="Звонков",
        )),
        Branch("top5", "Пятёрка лидеров", parent="rating", after="ranked",
               steps=({"id": "five", "type": "limit", "value": 5},)),
        Branch("inbound", "Входящие", after="with_employee", steps=(where("only_inbound", ONLY_INBOUND),)),
        Branch("talks", "Разговоры", after="with_employee", steps=(where("only_answered", (ANSWERED, "eq", True)),)),
    ),
    outputs=(
        Output("employees", "Сотрудников со звонками", "number", value=WHO, fn="count_distinct",
               format="integer"),
        Output("rating", "Рейтинг сотрудников", "table", branch="rating", limit=100),
        Output("by_employee", "Звонки по сотрудникам", "breakdown", by=WHO),
        Output("minutes_by_employee", "Минуты по сотрудникам", "breakdown", value=MINUTES, by=WHO),
        Output("missed_by_employee", "Пропущенные по сотрудникам", "breakdown", value=MISSED, by=WHO),
        Output("missed_share_by_employee", "Доля пропущенных по сотрудникам", "breakdown", value=MISSED_PERCENT,
               fn="avg", by=WHO, branch="inbound", format="percent"),
        Output("avg_talk_by_employee", "Средний разговор по сотрудникам, мин", "breakdown", value=MINUTES,
               fn="avg", by=WHO, branch="talks", format="decimal"),
        Output("top5", "Пятёрка лидеров", "breakdown", value="Звонков", fn="sum", by="Сотрудник",
               branch="top5"),
    ),
)

DEPARTMENTS_MONTH = DatasetTemplate(
    "departments_month", "Отделы за месяц", entity="calls",
    description="Звонки, разговоры, пропущенные и минуты по отделам за текущий месяц.",
    steps=(period("this_month"), EXTERNAL, where("with_department", (DEPARTMENT, "not_empty", None))),
    branches=(
        Branch("by_department", "По отделам", after="with_department", steps=summary(
            {DEPARTMENT: "Отдел"},
            count("Звонков"), total("Разговоров", ANSWERED), total("Пропущено", MISSED),
            total("Минут", MINUTES), {"key": "Сотрудников", "fn": "count_distinct", "field": "calls.operator_login"},
            rank="Звонков",
        )),
        Branch("inbound", "Входящие", after="with_department", steps=(where("only_inbound", ONLY_INBOUND),)),
    ),
    outputs=(
        Output("by_department", "Звонки по отделам", "breakdown", by=DEPARTMENT),
        Output("minutes_by_department", "Минуты по отделам", "breakdown", value=MINUTES, by=DEPARTMENT),
        Output("missed_by_department", "Пропущенные по отделам", "breakdown", value=MISSED, by=DEPARTMENT),
        Output("missed_share_by_department", "Доля пропущенных по отделам", "breakdown", value=MISSED_PERCENT, fn="avg",
               by=DEPARTMENT, branch="inbound", format="percent"),
        Output("departments", "Отделы", "table", branch="by_department", limit=50),
    ),
)

# Звонки к сотрудникам — связью: так в отчёт попадают и те, у кого звонков не было.
# Период у связанного набора не задаётся, поэтому коннектор отдаёт звонки
# за 30 дней по умолчанию.
EMPLOYEE_ACTIVITY = DatasetTemplate(
    "employee_activity", "Активность сотрудников за 30 дней", entity="operators",
    description="Все сотрудники АТС и их звонки за последние 30 дней; кто не звонил и не принимал звонков — в ветке.",
    joins=(Join("calls", "c", on=(("operators.login", "operator_login"),)),),
    steps=(
        {"id": "summary", "type": "group", "by": ["operators.name", "operators.position"], "aggregates": [
            {"key": "Звонков", "fn": "count_distinct", "field": "c.id"},
            {"key": "Минут", "fn": "sum", "field": "c.minutes"},
            {"key": "Последний звонок", "fn": "max", "field": "c.date"},
        ]},
        {"id": "named", "type": "fields", "columns": [
            {"key": "operators.name", "label": "Сотрудник"}, {"key": "operators.position", "label": "Должность"},
            {"key": "Звонков"}, {"key": "Минут"}, {"key": "Последний звонок", "date_format": "datetime"},
        ]},
        {"id": "ranked", "type": "sort", "rules": [{"field": "Звонков", "dir": "asc"}]},
    ),
    branches=(
        Branch("idle", "Без звонков", after="ranked", steps=(where("no_calls", ("Звонков", "eq", 0)),)),
    ),
    outputs=(
        Output("operators", "Сотрудников в АТС", "number", format="integer"),
        Output("idle", "Сотрудников без звонков", "number", branch="idle", format="integer"),
        Output("idle_list", "Сотрудники без звонков", "table", branch="idle", columns=("Сотрудник", "Должность"),
               limit=100),
        Output("activity", "Активность сотрудников", "table", limit=100),
    ),
)

CLIENTS_MONTH = DatasetTemplate(
    "clients_month", "Клиенты за месяц", entity="calls",
    description="Номера клиентов текущего месяца: сколько уникальных, кто звонит чаще всех и "
                "кого потеряли — звонил, не дозвонился, и ему не перезвонили.",
    steps=(period("this_month"), EXTERNAL, where("with_client", (CLIENT, "not_empty", None))),
    branches=(
        Branch("by_client", "По номерам", after="with_client", steps=(
            {"id": "summary", "type": "group", "by": [CLIENT], "aggregates": [
                count("Звонков"), total("Входящих", INBOUND), total("Пропущено", MISSED),
                total("Разговоров", ANSWERED), total("Минут", MINUTES),
                {"key": "Последний звонок", "fn": "max", "field": CALL_AT},
            ]},
            {"id": "named", "type": "fields", "columns": [
                {"key": CLIENT, "label": "Номер"}, {"key": "Звонков"}, {"key": "Входящих"},
                {"key": "Пропущено"}, {"key": "Разговоров"}, {"key": "Минут"},
                {"key": "Последний звонок", "date_format": "datetime"},
            ]},
        )),
        Branch("lost", "Потерянные", parent="by_client", after="named", steps=(
            where("not_reached", ("Пропущено", "gt", 0), ("Разговоров", "eq", 0)),
            {"id": "latest", "type": "sort", "rules": [{"field": "Последний звонок", "dir": "desc"}]},
        )),
        Branch("frequent", "Звонят чаще всех", parent="by_client", after="named", steps=(
            {"id": "ranked", "type": "sort", "rules": [{"field": "Звонков", "dir": "desc"}]},
            {"id": "top", "type": "limit", "value": 20},
        )),
    ),
    outputs=(
        Output("clients", "Клиентов за месяц", "number", value=CLIENT, fn="count_distinct", format="integer"),
        Output("clients_by_day", "Клиенты по дням", "series", value=CLIENT, fn="count_distinct", by=CALL_AT),
        Output("lost", "Потерянных клиентов", "number", branch="lost", format="integer"),
        Output("lost_list", "Потерянные клиенты", "table", branch="lost",
               columns=("Номер", "Пропущено", "Последний звонок"), limit=200),
        Output("frequent", "Звонят чаще всех", "table", branch="frequent", limit=20),
        Output("frequent_chart", "Топ номеров по звонкам", "breakdown", value="Звонков", fn="sum", by="Номер",
               branch="frequent"),
    ),
)

DATASETS = (
    CALLS_TODAY, CALLBACK_TODAY, OUTBOUND_TODAY, CALLS_WEEK,
    CALLS_MONTH, CALLS_LAST_MONTH, INBOUND_MONTH, MISSED_MONTH, LINES_MONTH, LOAD_30_DAYS,
    OUTBOUND_MONTH, EMPLOYEES_MONTH, DEPARTMENTS_MONTH, EMPLOYEE_ACTIVITY, CLIENTS_MONTH,
)

# --------------------------------------------------------------------------- #
# Виджеты: по одному на показатель, который стоит показать на дашборде.
# --------------------------------------------------------------------------- #

INTEGER = {"number_format": "integer"}
PERCENT = {"number_format": "percent"}
DECIMAL = {"number_format": "decimal"}


def kpi(widget_id: str, title: str, dataset: str, output: str, options: dict = INTEGER) -> WidgetTemplate:
    return WidgetTemplate(widget_id, title, "kpi", dataset=dataset, output=output, options=dict(options))


def chart(widget_id: str, title: str, dataset: str, output: str, chart_type: str, **kw) -> WidgetTemplate:
    return WidgetTemplate(widget_id, title, "chart", dataset=dataset, output=output, chart_type=chart_type, **kw)


def table(widget_id: str, title: str, dataset: str, output: str, **kw) -> WidgetTemplate:
    return WidgetTemplate(widget_id, title, "table", dataset=dataset, output=output, **kw)


WIDGETS = (
    # Сегодня
    kpi("today_calls", "Звонков сегодня", "calls_today", "calls"),
    kpi("today_answered", "Разговоров сегодня", "calls_today", "answered"),
    kpi("today_missed", "Пропущено сегодня", "calls_today", "missed"),
    kpi("today_minutes", "Минут сегодня", "calls_today", "minutes"),
    kpi("today_missed_share", "Доля пропущенных сегодня", "calls_today", "missed_share", PERCENT),
    chart("today_by_hour", "Звонки сегодня по часам", "calls_today", "by_hour", "bar"),
    table("today_employees", "Сотрудники сегодня", "calls_today", "employees"),
    kpi("callback_waiting", "Ждут звонка", "callback_today", "waiting"),
    table("callback_numbers", "Кому перезвонить", "callback_today", "numbers",
          description="Не дозвонились сегодня, разговора с ними ещё не было"),
    table("callback_missed_calls", "Пропущенные звонки сегодня", "callback_today", "missed_calls"),
    kpi("outbound_today_attempts", "Исходящих сегодня", "outbound_today", "attempts"),
    kpi("outbound_today_reach", "Дозвон сегодня", "outbound_today", "reach_share", PERCENT),
    chart("outbound_today_by_employee", "Обзвон сегодня по сотрудникам", "outbound_today", "by_employee", "bar_h"),
    table("outbound_today_employees", "Обзвон сегодня: сотрудники", "outbound_today", "employees"),
    # 7 дней
    chart("week_by_day", "Звонки за 7 дней", "calls_week", "by_day", "bar"),
    chart("week_missed_by_day", "Пропущенные за 7 дней", "calls_week", "missed_by_day", "bar"),
    # Месяц
    kpi("month_calls", "Звонков за месяц", "calls_month", "calls"),
    kpi("month_answered", "Разговоров за месяц", "calls_month", "answered"),
    kpi("month_missed", "Пропущено за месяц", "calls_month", "missed"),
    kpi("month_minutes", "Минут за месяц", "calls_month", "minutes"),
    kpi("month_missed_share", "Доля пропущенных за месяц", "calls_month", "missed_share", PERCENT),
    chart("month_by_day", "Звонки по дням", "calls_month", "by_day", "area", w=8),
    chart("month_by_type", "Входящие и исходящие", "calls_month", "by_type", "half_doughnut", w=4),
    chart("month_by_result", "Чем закончились звонки", "calls_month", "by_result", "doughnut"),
    chart("month_top5", "Пятёрка лидеров по звонкам", "calls_month", "top5", "bar_h"),
    chart("month_missed_by_employee", "Кто пропускает звонки", "calls_month", "missed_by_employee", "bar_h"),
    kpi("last_month_calls", "Звонков за прошлый месяц", "calls_last_month", "calls"),
    kpi("last_month_minutes", "Минут за прошлый месяц", "calls_last_month", "minutes"),
    kpi("last_month_missed_share", "Доля пропущенных за прошлый месяц", "calls_last_month", "missed_share",
        PERCENT),
    # Входящие
    kpi("inbound_calls", "Входящих за месяц", "inbound_month", "calls"),
    kpi("inbound_answered", "Принято за месяц", "inbound_month", "answered"),
    kpi("inbound_missed", "Пропущено за месяц", "inbound_month", "missed"),
    kpi("inbound_missed_share", "Доля пропущенных", "inbound_month", "missed_share", PERCENT),
    chart("inbound_by_day", "Входящие по дням", "inbound_month", "by_day", "area", w=8),
    chart("inbound_by_status", "Чем закончились входящие", "inbound_month", "by_status", "doughnut", w=4),
    chart("inbound_missed_share_by_day", "Доля пропущенных по дням", "inbound_month", "missed_share_by_day",
          "line", options={**PERCENT, "smooth": True}),
    chart("missed_by_hour", "Пропущенные по часам", "missed_month", "by_hour", "bar"),
    chart("missed_by_weekday", "Пропущенные по дням недели", "missed_month", "by_weekday", "bar"),
    chart("missed_by_line", "Пропущенные по линиям", "missed_month", "by_line", "bar_h"),
    table("missed_list", "Пропущенные звонки за месяц", "missed_month", "list", w=12, h=5),
    table("lines", "Многоканальные номера", "lines_month", "lines"),
    chart("lines_by_line", "Звонки по номерам", "lines_month", "by_line", "bar_h"),
    chart("load_by_hour", "Нагрузка по часам", "load_30_days", "by_hour", "bar"),
    chart("load_by_weekday", "Нагрузка по дням недели", "load_30_days", "by_weekday", "bar"),
    table("load_grid", "Нагрузка: день недели × час", "load_30_days", "grid"),
    # Исходящие
    kpi("outbound_attempts", "Исходящих за месяц", "outbound_month", "attempts"),
    kpi("outbound_reached", "Дозвонов за месяц", "outbound_month", "reached"),
    kpi("outbound_reach_share", "Доля дозвона", "outbound_month", "reach_share", PERCENT),
    kpi("outbound_avg_talk", "Средний разговор, мин", "outbound_month", "avg_talk", DECIMAL),
    chart("outbound_by_day", "Исходящие по дням", "outbound_month", "by_day", "bar", w=8),
    chart("outbound_result", "Дозвонились или нет", "outbound_month", "by_result", "half_doughnut", w=4),
    chart("outbound_reach_by_employee", "Доля дозвона по сотрудникам", "outbound_month", "reach_by_employee",
          "bar_h", w=4, options=PERCENT),
    table("outbound_employees", "Обзвон: сотрудники", "outbound_month", "employees", w=8),
    chart("outbound_short_by_employee", "Разговоры короче 10 секунд", "outbound_month", "short_by_employee",
          "bar_h", w=4),
    # Сотрудники и отделы
    kpi("employees_count", "Сотрудников со звонками", "employees_month", "employees"),
    table("employees_rating", "Рейтинг сотрудников", "employees_month", "rating", w=12, h=5),
    chart("employees_missed_share", "Доля пропущенных по сотрудникам", "employees_month",
          "missed_share_by_employee", "bar_h", options=PERCENT),
    chart("employees_avg_talk", "Средний разговор по сотрудникам, мин", "employees_month",
          "avg_talk_by_employee", "bar_h", options=DECIMAL),
    table("departments", "Отделы", "departments_month", "departments"),
    chart("departments_by_calls", "Звонки по отделам", "departments_month", "by_department", "pie"),
    kpi("idle_count", "Сотрудников без звонков", "employee_activity", "idle"),
    table("idle_list", "Сотрудники без звонков за 30 дней", "employee_activity", "idle_list"),
    table("employee_activity", "Активность сотрудников за 30 дней", "employee_activity", "activity"),
    # Клиенты
    kpi("clients_count", "Клиентов за месяц", "clients_month", "clients"),
    kpi("lost_count", "Потерянных клиентов", "clients_month", "lost"),
    chart("clients_by_day", "Клиенты по дням", "clients_month", "clients_by_day", "area", w=12),
    table("lost_clients", "Потерянные клиенты", "clients_month", "lost_list",
          description="Звонили в этом месяце, не дозвонились, и им не перезвонили"),
    table("frequent_clients", "Звонят чаще всех", "clients_month", "frequent"),
)

# --------------------------------------------------------------------------- #
# Дашборды. Сетка — 12 колонок, строка — 90 точек; kpi — 3×2, остальное 6×4.
# --------------------------------------------------------------------------- #


def kpi_row(y: int, *widgets: str) -> tuple[Place, ...]:
    return tuple(Place(w, x * 3, y) for x, w in enumerate(widgets))


DASHBOARDS = (
    DashboardTemplate(
        "pulse", "Пульс дня",
        description="Что происходит сегодня: звонки, кому перезвонить, нагрузка по часам и обзвон.",
        widgets=(
            *kpi_row(0, "today_calls", "today_answered", "today_missed", "callback_waiting"),
            Place("callback_numbers", 0, 2, h=5),
            Place("today_by_hour", 6, 2, h=5),
            Place("today_employees", 0, 7),
            Place("outbound_today_by_employee", 6, 7),
        ),
    ),
    DashboardTemplate(
        "month", "Итоги месяца",
        description="Текущий месяц против прошлого, лидеры и потерянные клиенты.",
        widgets=(
            *kpi_row(0, "month_calls", "month_minutes", "month_missed", "month_missed_share"),
            Place("month_by_day", 0, 2),
            Place("month_by_type", 8, 2),
            *kpi_row(6, "last_month_calls", "last_month_minutes", "clients_count", "lost_count"),
            Place("month_top5", 0, 8),
            Place("lost_clients", 6, 8),
        ),
    ),
    DashboardTemplate(
        "inbound", "Входящие",
        description="Входящие за месяц: пропущенные, когда и на каких линиях теряются звонки.",
        widgets=(
            *kpi_row(0, "inbound_calls", "inbound_answered", "inbound_missed", "inbound_missed_share"),
            Place("inbound_by_day", 0, 2),
            Place("inbound_by_status", 8, 2),
            Place("missed_by_hour", 0, 6),
            Place("missed_by_weekday", 6, 6),
            Place("lines", 0, 10),
            Place("load_by_hour", 6, 10),
            Place("missed_list", 0, 14),
        ),
    ),
    DashboardTemplate(
        "outbound", "Обзвон",
        description="Исходящие за месяц: попытки, дозвон, разговоры по сотрудникам.",
        widgets=(
            *kpi_row(0, "outbound_attempts", "outbound_reached", "outbound_reach_share", "outbound_avg_talk"),
            Place("outbound_by_day", 0, 2),
            Place("outbound_result", 8, 2),
            Place("outbound_employees", 0, 6, h=8),
            Place("outbound_reach_by_employee", 8, 6),
            Place("outbound_short_by_employee", 8, 10),
        ),
    ),
    DashboardTemplate(
        "employees", "Сотрудники",
        description="Рейтинг сотрудников и отделов за месяц, кто пропускает и кто не звонил.",
        widgets=(
            *kpi_row(0, "employees_count", "idle_count", "month_missed", "month_minutes"),
            Place("employees_rating", 0, 2),
            Place("employees_missed_share", 0, 7),
            Place("employees_avg_talk", 6, 7),
            Place("departments", 0, 11),
            Place("idle_list", 6, 11),
        ),
    ),
    DashboardTemplate(
        "clients", "Клиенты",
        description="Уникальные клиенты, потерянные и самые частые номера за месяц.",
        widgets=(
            *kpi_row(0, "clients_count", "lost_count", "month_missed", "inbound_missed_share"),
            Place("clients_by_day", 0, 2),
            Place("lost_clients", 0, 6, h=5),
            Place("frequent_clients", 6, 6, h=5),
        ),
    ),
)

QUICK_START = QuickStart(
    source_name="Телефония Мегафон",
    description="Источник и дашборды «Пульс дня» и «Итоги месяца».",
    dashboards=("pulse", "month"),
)

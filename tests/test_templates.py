"""Тесты готовых датасетов, виджетов и дашбордов (megafon_templates.py).

SDK проверяет форму шаблонов, но не имена колонок: опечатка в «calls.is_mised»
или в подписи «Сотрудник» всплыла бы только на платформе. Здесь колонки
прослеживаются по шагам каждой цепочки, а логика «кому перезвонить» прогоняется
на звонках из тестов коннектора небольшим исполнителем шагов.
"""
from __future__ import annotations

try:
    import pytest
except ImportError:
    class _Pytest:
        @staticmethod
        def fixture(fn):
            return fn
        class mark:
            @staticmethod
            def parametrize(*args, **kwargs):
                return lambda fn: fn
        @staticmethod
        def raises(exc):
            class _Raises:
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc_val, exc_tb):
                    return exc_type is not None and issubclass(exc_type, exc)
            return _Raises()
    pytest = _Pytest()

import megafon_templates as t
from infopanel_connect.templates import problems_of
from megafon_connector import COLUMNS, MegafonConnector, _call_row

DATASETS = {d.id: d for d in t.DATASETS}


def test_sdk_accepts_templates():
    assert problems_of(MegafonConnector()) == []


def test_fifteen_views_all_on_widgets():
    assert len(t.DATASETS) == 15
    assert {w.dataset for w in t.WIDGETS} == set(DATASETS)  # у каждого датасета есть виджет


def test_quick_start():
    assert t.QUICK_START.dashboards == ("pulse", "month")


# --------------------------------------------------------------------------- #
# Колонки по шагам
# --------------------------------------------------------------------------- #

def source_columns(dataset) -> set[str]:
    columns = {f"{dataset.base_alias}.{c.name}" for c in COLUMNS[dataset.entity]}
    for join in dataset.joins:
        columns |= {f"{join.alias}.{c.name}" for c in COLUMNS[join.entity]}
    return columns


def walk(steps, columns: set[str], where: str) -> dict[str, set[str]]:
    """Колонки после каждого шага; ссылки на несуществующие колонки — ошибка."""
    after: dict[str, set[str]] = {"": set(columns)}

    def need(field: str, what: str) -> None:
        assert field in columns, f"{where}: {what} {field!r} нет среди {sorted(columns)}"

    for index, step in enumerate(steps, start=1):
        kind = step["type"]
        if kind == "period":
            need(step["field"], "период по")
        elif kind == "filter":
            for cond in step["conditions"]:
                need(cond["field"], "условие на")
        elif kind == "sort":
            for rule in step["rules"]:
                need(rule["field"], "сортировка по")
        elif kind == "group":
            for field in step["by"]:
                need(field, "группировка по")
            for agg in step["aggregates"]:
                if agg["fn"] != "count":
                    need(agg["field"], f"{agg['fn']} по")
            columns = set(step["by"]) | {a["key"] for a in step["aggregates"]}
        elif kind == "fields":
            for col in step["columns"]:
                need(col["key"], "колонка")
            columns = {c.get("label") or c["key"] for c in step["columns"]}
        elif kind == "computed":
            columns = columns | {c["key"] for c in step["columns"]}
        after[step.get("id") or f"s{index}"] = set(columns)
    after["*"] = set(columns)
    return after


@pytest.mark.parametrize("dataset", t.DATASETS, ids=lambda d: d.id)
def test_columns_exist(dataset):
    chains = {"": walk(dataset.steps, source_columns(dataset), dataset.id)}
    for branch in dataset.branches:  # родители объявлены раньше детей
        start = chains[branch.parent][branch.after or ""]
        chains[branch.id] = walk(branch.steps, start, f"{dataset.id}/{branch.id}")
    for output in dataset.outputs:
        result = chains[output.branch]["*"]
        for field in (output.value, output.by, *output.columns):
            if field:
                assert field in result, f"{dataset.id}.{output.id}: нет колонки {field!r} среди {sorted(result)}"


PERCENT_COLUMNS = {"calls.answered_percent", "calls.missed_percent"}


@pytest.mark.parametrize("dataset", t.DATASETS, ids=lambda d: d.id)
def test_percents_are_averages_of_100_0_flags(dataset):
    """Формат percent только дописывает «%»: доля — среднее колонки 100/0, а не флага 1/0."""
    for output in dataset.outputs:
        if output.format == "percent":
            assert (output.fn, output.value) in {("avg", c) for c in PERCENT_COLUMNS}, f"{dataset.id}.{output.id}"
        if output.value in PERCENT_COLUMNS:
            assert output.format == "percent", f"{dataset.id}.{output.id}: процент без format=percent"


def test_percent_widgets_show_percent_outputs():
    outputs = {(d.id, o.id): o for d in t.DATASETS for o in d.outputs}
    for widget in t.WIDGETS:
        output = outputs[(widget.dataset, widget.output)]
        is_percent = widget.options.get("number_format") == "percent"
        assert is_percent == (output.format == "percent"), widget.id


# --------------------------------------------------------------------------- #
# Дашборды
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dashboard", t.DASHBOARDS, ids=lambda d: d.id)
def test_dashboard_widgets_do_not_overlap(dashboard):
    widgets = {w.id: w for w in t.WIDGETS}
    taken: dict[tuple[int, int], str] = {}
    for place in dashboard.widgets:
        size = place.as_dict(widgets)
        assert size["x"] + size["w"] <= 12, f"{place.widget} выходит за 12 колонок"
        for x in range(size["x"], size["x"] + size["w"]):
            for y in range(size["y"], size["y"] + size["h"]):
                assert (x, y) not in taken, f"{place.widget} налезает на {taken[(x, y)]}"
                taken[(x, y)] = place.widget
    assert len({p.widget for p in dashboard.widgets}) == len(dashboard.widgets)


# --------------------------------------------------------------------------- #
# Логика: маленький исполнитель шагов (без периода) на строках коннектора
# --------------------------------------------------------------------------- #

OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b, "lt": lambda a, b: a is not None and a < b,
    "empty": lambda a, _: a in (None, ""), "not_empty": lambda a, _: a not in (None, ""),
}
FNS = {
    "count": lambda rows, f: len(rows),
    "sum": lambda rows, f: sum(r[f] or 0 for r in rows),
    "max": lambda rows, f: max((r[f] for r in rows if r[f] is not None), default=None),
    "count_distinct": lambda rows, f: len({r[f] for r in rows if r[f] is not None}),
}


def run(steps, rows: list[dict]) -> list[dict]:
    for step in steps:
        kind = step["type"]
        if kind == "filter":
            rows = [r for r in rows if all(OPS[c["op"]](r.get(c["field"]), c.get("value"))
                                           for c in step["conditions"])]
        elif kind == "group":
            groups: dict[tuple, list[dict]] = {}
            for row in rows:
                groups.setdefault(tuple(row[f] for f in step["by"]), []).append(row)
            rows = [{**dict(zip(step["by"], key)),
                     **{a["key"]: FNS[a["fn"]](items, a.get("field")) for a in step["aggregates"]}}
                    for key, items in groups.items()]
        elif kind == "fields":
            rows = [{c.get("label") or c["key"]: r[c["key"]] for c in step["columns"]} for r in rows]
        elif kind == "sort":
            for rule in reversed(step["rules"]):
                rows = sorted(rows, key=lambda r: r[rule["field"]], reverse=rule["dir"] == "desc")
        elif kind == "limit":
            rows = rows[: step["value"]]
    return rows


CALL_IN = {
    "communication_id": 101,
    "call_start_date_time": "2026-10-01 10:00:00",
    "call_direction": {"value_id": "in"},
    "call_status": {"value_id": "success"},
    "contact_phone_number": {"value": "79000000001"},
    "virtual_phone_number": "74950000001",
    "employees": [{"value": "Иванов Иван"}],
    "user": "ivanov",
    "group_name": "Отдел продаж",
    "call_talk_duration": "00:00:23",
    "call_total_duration": "00:00:28",
    "call_is_internal": "external",
}

CALL_OUT = {
    "communication_id": 102,
    "call_start_date_time": "2026-10-01 11:00:00",
    "call_direction": {"value_id": "out"},
    "call_status": {"value_id": "missed"},
    "contact_phone_number": {"value": "79000000001"},
    "employees": [{"value": "Петров Пётр"}],
    "user": "petrov",
    "group_name": "Отдел продаж",
    "call_talk_duration": "00:00:00",
    "call_total_duration": "00:00:15",
    "call_is_internal": "external",
}

CALL_MISSED = {
    "communication_id": 103,
    "call_start_date_time": "2026-10-01 12:00:00",
    "call_direction": {"value_id": "in"},
    "call_status": {"value_id": "missed"},
    "contact_phone_number": {"value": "79990000001"},
    "virtual_phone_number": "74950000001",
    "employees": [{"value": "Иванов Иван"}],
    "user": "ivanov",
    "group_name": "Отдел продаж",
    "call_talk_duration": "00:00:00",
    "call_total_duration": "00:00:10",
    "call_is_internal": "external",
}


def rows_of(*calls: dict) -> list[dict]:
    return [{f"calls.{k}": v for k, v in _call_row(c).items()} for c in calls]


def branch_rows(dataset, branch_id: str, rows: list[dict]) -> list[dict]:
    """Строки ветки: шаги основной цепочки до after, затем шаги ветки (с родителями)."""
    branches = {b.id: b for b in dataset.branches}
    chain = []
    current = branches[branch_id]
    while current:
        chain.append(current)
        current = branches.get(current.parent)
    steps = list(dataset.steps)
    for branch in reversed(chain):
        if branch.after:
            ids = [s.get("id") for s in steps]
            steps = steps[: ids.index(branch.after) + 1]
        steps += list(branch.steps)
    return run(steps, rows)


def test_callback_today_lists_numbers_without_conversation():
    numbers = run(t.CALLBACK_TODAY.steps, rows_of(CALL_IN, CALL_OUT, CALL_MISSED))
    assert [n["Номер"] for n in numbers] == ["79990000001"]
    assert numbers[0]["Пропущено"] == 1


def test_callback_disappears_after_successful_callback():
    callback = {
        **CALL_OUT,
        "communication_id": 104,
        "call_status": {"value_id": "success"},
        "call_talk_duration": "00:01:00",
        "contact_phone_number": {"value": "79990000001"},
        "call_start_date_time": "2026-10-01 13:00:00",
    }
    assert run(t.CALLBACK_TODAY.steps, rows_of(CALL_IN, CALL_MISSED, callback)) == []


def test_internal_calls_are_not_clients():
    internal = {
        **CALL_MISSED,
        "communication_id": 105,
        "call_is_internal": "internal",
        "contact_phone_number": {"value": "201"},
    }
    numbers = run(t.CALLBACK_TODAY.steps, rows_of(CALL_MISSED, internal))
    assert [n["Номер"] for n in numbers] == ["79990000001"]


def test_lost_and_frequent_clients():
    rows = rows_of(CALL_IN, CALL_OUT, CALL_MISSED)
    lost = branch_rows(t.CLIENTS_MONTH, "lost", rows)
    assert [r["Номер"] for r in lost] == ["79990000001"]
    frequent = branch_rows(t.CLIENTS_MONTH, "frequent", rows)
    assert [(r["Номер"], r["Звонков"], r["Разговоров"]) for r in frequent] == [
        ("79000000001", 2, 1), ("79990000001", 1, 0)]


def test_missed_by_employee_and_rating():
    rows = rows_of(CALL_IN, CALL_OUT, CALL_MISSED)
    who_misses = branch_rows(t.CALLS_MONTH, "missed_by_employee", rows)
    assert who_misses == [{"Сотрудник": "Иванов Иван", "Пропущено": 1}]
    rating = {r["Сотрудник"]: r for r in branch_rows(t.EMPLOYEES_MONTH, "rating", rows)}
    assert (rating["Иванов Иван"]["Звонков"], rating["Иванов Иван"]["Входящих"]) == (2, 2)
    assert (rating["Иванов Иван"]["Разговоров"], rating["Иванов Иван"]["Пропущено"]) == (1, 1)
    assert (rating["Петров Пётр"]["Звонков"], rating["Петров Пётр"]["Входящих"]) == (1, 0)


def test_outbound_result_has_two_values():
    """«Дозвонились или нет» — разбивка исходящих по статусу: ровно два значения."""
    placed = {**CALL_OUT, "communication_id": 106, "call_status": {"value_id": "success"}}
    rows = run(t.OUTBOUND_MONTH.steps, rows_of(CALL_IN, CALL_OUT, CALL_MISSED, placed))
    by = next(o.by for o in t.OUTBOUND_MONTH.outputs if o.id == "by_result")
    assert sorted(r[by] for r in rows) == ["Дозвонился", "Не дозвонился"]


def test_missed_share_is_in_percent():
    """2 входящих, 1 пропущен → среднее missed_percent = 50, на виджете «50%»."""
    rows = run(t.INBOUND_MONTH.steps, rows_of(CALL_IN, CALL_OUT, CALL_MISSED))
    values = [r["calls.missed_percent"] for r in rows]
    assert sum(values) / len(values) == 50

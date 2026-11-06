# Коннектор Инфопанели: телефония Мегафон

Выгружает данные виртуальной АТС Мегафон:
- **Мегафон ВАТС 2.0** (`simple-api.cp.megafon.ru` и `data-api.cp.megafon.ru`, авторизация JWT-токеном);
- **Мегафон ВАТС 1.0 legacy** (`api.megapbx.ru`, CRM API v1).

Построен на SDK [infopanel-connect](https://gitlab.itgrn.ru/infopanel/connector-sdk).

| Сущность | Метод API (ВАТС 2.0) | Метод API (ВАТС 1.0) | Связь |
|---|---|---|---|
| `calls` — звонки | JSON-RPC 2.0 `get.mgf_calls` | `GET /crmapi/v1/history/json` | `operator_login` → `operators.login` |
| `operators` — сотрудники | `GET /employee` | `GET /crmapi/v1/users?with=status` | `login` |
| `groups` — отделы | `GET /employee-group` | `GET /crmapi/v1/groups` | `id` |
| `group_members` — состав отделов | `GET /employee-group` + `/employee` | `GET /crmapi/v1/groups` (`users[]`) | `group_id` → `groups.id`, `login` → `operators.login` |

Логические признаки отдаются числами 1/0 (логического типа у колонок SDK нет).

```
megafon_connector.py   коннектор (класс MegafonConnector)
tests/                 тесты (API подменяется примерами ответов)
requirements.txt       зависимости: infopanel-connect>=2.2,<3
requirements-dev.txt   + pytest
Dockerfile             образ сервиса
.github/workflows/     CI: тесты на Python 3.10–3.12 и сборка образа
```

## Колонки

### `calls` — Звонки

| Колонка | Тип | Описание |
|---|---|---|
| `id` | string | ID звонка |
| `date` | datetime | Дата и время начала |
| `direction` | string | Направление (in/out) |
| `status` | string | Статус (success/missed/noanswer) |
| `client_number` | string | Номер клиента |
| `diversion` | string | Номер ВАТС, по которому пришёл звонок |
| `telnum_name` | string | Имя номера ВАТС |
| `destination` | string | Адресат входящего звонка |
| `operator_login` | string | Логин сотрудника |
| `operator_name` | string | Сотрудник |
| `group_name` | string | Отдел |
| `wait` | number | Ожидание на линии, сек |
| `duration` | number | Длительность разговора, сек |
| `record_url` | string | Ссылка на запись разговора |
| `rating` | number | Оценка качества обслуживания |
| `note` | string | Примечание |
| `missed_status` | string | Статус пропущенного звонка |

### `operators` — Сотрудники

| Колонка | Тип | Описание |
|---|---|---|
| `login` | string | Логин |
| `name` | string | Имя |
| `position` | string | Должность |
| `email` | string | Электронная почта |
| `ext` | string | Внутренний номер |
| `telnum` | string | Прямой номер |
| `mobile` | string | Мобильный телефон |
| `role` | string | Права в системе |
| `status` | string | Статус регистрации (на момент выгрузки) |
| `mobile_redirect` | number | Переадресация на мобильный |
| `mobile_redirect_forward` | number | Переадресация на мобильный: forward |
| `mobile_redirect_delay` | number | Задержка переадресации на мобильный, сек |

### `groups` — Отделы

| Колонка | Тип | Описание |
|---|---|---|
| `id` | string | ID отдела |
| `name` | string | Отдел |
| `ext` | string | Внутренний номер |
| `call_order` | string | Распределение звонков |
| `call_duration` | number | Интервал дозвона, сек |
| `timeout_time` | number | Если никто не отвечает: через, сек |
| `timeout_target` | string | Если никто не отвечает: куда |
| `advanced` | string | Для нетерпеливых клиентов |
| `queue_position` | number | Сообщать номер в очереди |
| `users_count` | number | Сотрудников в отделе |

### `group_members` — Состав отделов

| Колонка | Тип | Описание |
|---|---|---|
| `group_id` | string | ID отдела |
| `group_name` | string | Отдел |
| `login` | string | Логин сотрудника |
| `calls_enable` | number | Принимает звонки отдела |
| `group_head` | number | Руководитель отдела |
| `is_absence_active` | number | Отсутствует |
| `is_forwarding_disabled` | number | Переадресация отключена |

## Запуск без Docker

```bash
pip install -r requirements.txt \
  --index-url https://gitlab.itgrn.ru/api/v4/projects/infopanel%2Fconnector-sdk/packages/pypi/simple \
  --extra-index-url https://pypi.org/simple
infopanel-connect check megafon_connector:MegafonConnector --secrets secrets.json  # {"api_key": "<ключ>"}
INFOPANEL_SIGNING_SECRET=dev infopanel-connect serve megafon_connector:MegafonConnector --port 8090
```

Тесты: `pip install -r requirements-dev.txt` (с тем же `--index-url`), затем `pytest -q`.

Для правок SDK одновременно с коннектором: `pip install -e ../connector-sdk`.

## Docker

```bash
docker build -t call-reports-api-megafon .
docker run --rm -p 8090:8090 -e INFOPANEL_SIGNING_SECRET=dev call-reports-api-megafon
curl http://localhost:8090/health
```

## Подключение источника

- **Домен АТС Мегафона** — по умолчанию `simple-api.cp.megafon.ru` (для ВАТС 2.0); для ВАТС 1.0 укажите `api.megapbx.ru`;
- **Ключ для авторизации в АТС** — из личного кабинета Мегафона (Интеграция с CRM → API → «Сгенерировать ключ»).

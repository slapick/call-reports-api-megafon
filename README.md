# Коннектор Инфопанели: телефония Мегафон

Выгружает данные виртуальной АТС Мегафон через CRM API
(https://api.megapbx.ru/#/docs/crmapi/v1).
Построен на SDK [infopanel-connect](https://gitlab.itgrn.ru/infopanel/connector-sdk).

| Сущность | Метод API | Связь |
|---|---|---|
| `calls` — звонки | `GET /crmapi/v1/history/json` | `operator_login` → `operators.login` |
| `operators` — сотрудники | `GET /crmapi/v1/users?with=status` | `login` |
| `groups` — отделы | `GET /crmapi/v1/groups` | `id` |
| `group_members` — состав отделов | `GET /crmapi/v1/groups` (`users[]`) | `group_id` → `groups.id`, `login` → `operators.login` |

Логические признаки отдаются числами 1/0 (логического типа у колонок SDK нет).

```
megafon_connector.py   коннектор (класс MegafonConnector)
tests/                 тесты (CRM API подменяется примерами ответов)
requirements.txt       зависимости: infopanel-connect>=2.2,<3
requirements-dev.txt   + pytest
Dockerfile             образ сервиса
.github/workflows/     CI: тесты на Python 3.10–3.12 и сборка образа
```

## Запуск без Docker

```bash
pip install -r requirements.txt \
  --index-url https://gitlab.itgrn.ru/api/v4/projects/94/packages/pypi/simple \
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

- **Домен АТС Мегафона** — по умолчанию `api.megapbx.ru`;
- **Ключ для авторизации в АТС** — из личного кабинета Мегафона (настройки интеграции).

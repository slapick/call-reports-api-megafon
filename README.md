# Коннектор Инфопанели: телефония Мегафон

Выгружает звонки виртуальной АТС Мегафон через CRM API
(`GET /crmapi/v1/history/json`, https://api.megapbx.ru/#/docs/crmapi/v1).
Построен на SDK [infopanel-connect](https://gitlab.itgrn.ru/infopanel/connector-sdk).

```
megafon_connector.py   коннектор (класс MegafonConnector)
requirements.txt       зависимости: infopanel-connect>=2.2,<3
Dockerfile             образ сервиса
```

## Запуск без Docker

```bash
pip install -r requirements.txt \
  --index-url https://gitlab.itgrn.ru/api/v4/projects/94/packages/pypi/simple \
  --extra-index-url https://pypi.org/simple
infopanel-connect check megafon_connector:MegafonConnector
INFOPANEL_SIGNING_SECRET=dev infopanel-connect serve megafon_connector:MegafonConnector --port 8090
```

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

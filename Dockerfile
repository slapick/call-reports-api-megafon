# Образ коннектора телефонии Мегафон. Сборка из корня репозитория:
#   docker build -t call-reports-api-megafon .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# SDK infopanel-connect — из Package Registry проекта connector-sdk.
ARG PIP_INDEX_URL=https://gitlab.itgrn.ru/api/v4/projects/infopanel%2Fconnector-sdk/packages/pypi/simple
ARG PIP_EXTRA_INDEX_URL=https://pypi.org/simple

COPY requirements.txt /app/requirements.txt
RUN pip install --index-url "$PIP_INDEX_URL" --extra-index-url "$PIP_EXTRA_INDEX_URL" -r /app/requirements.txt

COPY megafon_connector.py /app/megafon_connector.py
WORKDIR /app
RUN useradd --system --uid 10001 connector
USER connector

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4)"
# Секрет подписи — в переменной INFOPANEL_SIGNING_SECRET (несколько — через запятую).
CMD ["infopanel-connect", "serve", "megafon_connector:MegafonConnector", "--port", "8090"]

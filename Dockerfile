FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn==23.0.0

COPY . .

# Не от root. Папка приложения принадлежит пользователю — туда пишется кэш .cache/
RUN useradd --create-home app && chown -R app:app /app
USER app

# Площадка сама передаёт порт через PORT; локально — 8000.
ENV PORT=8000
EXPOSE 8000

# Один процесс, несколько потоков: кэш цен общий, и к MEXC уходит не больше
# одного запроса в 2 секунды, сколько бы людей ни держали страницу открытой.
CMD exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 60 --access-logfile -

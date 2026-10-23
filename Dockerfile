FROM python:3.13-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY ml ./ml
COPY scripts ./scripts

EXPOSE 8000
# Migrations run automatically on startup (app/main.py -> app.db.init_db).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

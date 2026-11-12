# Annual Reviews Portal: Backend

FastAPI + SQLAlchemy + Alembic (MySQL).

## Setup

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then fill in the values
alembic upgrade head
python -m app
```

The API runs on http://localhost:4000.

## API documentation

https://n8jspssafq.apidog.io/

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests don't need MySQL.

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

FROM python:3.12-slim

# Repo-root build context (see render.yaml): the image carries the API app
# plus the pipelines/ job modules and ml/ feature/model code so the
# in-process scheduler (app/main.py lifespan) can run the Phase 10 roster.
WORKDIR /srv/app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY pipelines ./pipelines
COPY ml ./ml

EXPOSE 8000

# Render and most PaaS hosts inject $PORT — honor it, defaulting to 8000
# for local `docker run` usage.
# All credentials come from environment (see ../../.env.example); none are baked in.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

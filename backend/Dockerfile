FROM python:3.12-slim

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Render and most PaaS hosts inject $PORT — honor it, defaulting to 8000
# for local `docker run` usage.
# All credentials come from environment (see ../../.env.example); none are baked in.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

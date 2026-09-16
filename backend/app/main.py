"""Money by Numbers API — FastAPI application entrypoint."""

from fastapi import FastAPI

from .config import get_settings
from .middleware import configure_cors
from .routers import admin, affiliate, auth, billing, data_health, game_context, health, numby, odds, playoffs, predictions, track_record

app = FastAPI(
    title="Money by Numbers API",
    version=get_settings().APP_VERSION,
)

# Phase 12: security headers, per-IP rate limits on abuse-sensitive public
# endpoints, and explicit-origin CORS (refuses wildcard in production).
configure_cors(app)

app.include_router(health.router)
app.include_router(data_health.router)
app.include_router(auth.router)
app.include_router(predictions.router)
app.include_router(odds.router)
app.include_router(game_context.router)
app.include_router(playoffs.router)
app.include_router(track_record.router)
app.include_router(numby.router)
app.include_router(affiliate.router)
app.include_router(admin.router)
app.include_router(billing.router)


@app.get("/")
def root():
    return {"service": "money-by-numbers", "version": get_settings().APP_VERSION}

"""FastAPI 入口。"""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import matches, predict, backtest

app = FastAPI(title="football-prediction-v2", version="1.0.0")
app.include_router(matches.router)
app.include_router(predict.router)
app.include_router(backtest.router)

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(STATIC):
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    idx = os.path.join(STATIC, "index.html")
    if os.path.exists(idx):
        return FileResponse(idx)
    return {"ok": True, "docs": "/docs"}


@app.get("/health")
def health():
    return {"ok": True}

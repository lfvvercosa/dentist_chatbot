"""FastAPI application. Run with: uv run uvicorn app.main:app --reload"""

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="Big Smile Clinic Assistant API", version="0.1.0")
app.include_router(router)

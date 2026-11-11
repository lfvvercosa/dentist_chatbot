"""FastAPI application. Run with: uv run uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.clinic_config import load_clinic_config
from app.core.settings import Settings
from app.llm.claude import build_claude_client
from app.prompts.system_prompt import build_system_prompt
from app.services.conversation import ConversationService
from app.services.escalation import EscalationService

# uvicorn configures only its own loggers; without this, our INFO lines are dropped.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once, before the first request. A broken clinic.yaml raises here,
    # so the server refuses to start instead of answering with wrong facts.
    settings = Settings()
    config = load_clinic_config()
    system_prompt = build_system_prompt(config)
    app.state.conversation_service = ConversationService(
        build_claude_client(settings), system_prompt, EscalationService(config.emergency)
    )
    yield


app = FastAPI(title="Big Smile Clinic Assistant API", version="1.0.0", lifespan=lifespan)
app.include_router(router)

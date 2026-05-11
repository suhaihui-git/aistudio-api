"""OpenAI-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from aistudio_api.application.api_service import handle_chat, handle_image_generation
from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .dependencies import get_client, require_api_key
from .schemas import ChatRequest, ImageRequest

router = APIRouter()

MODELS = [
    # Gemma 4 系列
    {"id": "gemma-4-31b-it", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemma-4-26b-a4b-it", "object": "model", "created": 1700000000, "owned_by": "google"},
    # Gemini 3 系列
    {"id": "gemini-3-flash-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-pro-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-image-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3-pro-image-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-live-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-3.1-flash-tts-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # Gemini 2.5 系列
    # {"id": "gemini-2.5-pro", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-image", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-pro-preview-tts", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "gemini-2.5-flash-preview-tts", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Gemini 2.0 系列
    # {"id": "gemini-2.0-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Latest 别名
    {"id": "gemini-pro-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-flash-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    {"id": "gemini-flash-lite-latest", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Deep Research
    # {"id": "deep-research-preview-04-2026", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "deep-research-max-preview-04-2026", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 图片生成 (Imagen)
    # {"id": "imagen-4.0-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "imagen-4.0-ultra-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "imagen-4.0-fast-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 视频生成 (Veo)
    # {"id": "veo-3.1-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-3.1-fast-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-3.1-lite-generate-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "veo-2.0-generate-001", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # 音乐生成 (Lyria)
    # {"id": "lyria-3-pro-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # {"id": "lyria-3-clip-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
    # # Robotics
    # {"id": "gemini-robotics-er-1.6-preview", "object": "model", "created": 1700000000, "owned_by": "google"},
]

MODEL_IDS = {m["id"] for m in MODELS}


@router.get("/v1/models")
async def list_models(auth=Depends(require_api_key)):
    return {"object": "list", "data": MODELS}


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str, auth=Depends(require_api_key)):
    for m in MODELS:
        if m["id"] == model_id:
            return m
    raise HTTPException(status_code=404, detail={"message": f"Model '{model_id}' not found", "type": "invalid_request_error"})


@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatRequest,
    client: AIStudioClient = Depends(get_client),
    auth=Depends(require_api_key),
):
    return await handle_chat(req, client)


@router.post("/v1/images/generations")
async def image_generations(
    req: ImageRequest,
    client: AIStudioClient = Depends(get_client),
    auth=Depends(require_api_key),
):
    return await handle_image_generation(req, client)


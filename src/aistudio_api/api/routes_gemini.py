"""Gemini-compatible API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aistudio_api.application.api_service import handle_gemini_generate_content
from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .dependencies import get_client, require_api_key
from .schemas import GeminiGenerateContentRequest

router = APIRouter()


@router.post("/v1beta/{model_path:path}:generateContent")
async def generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient = Depends(get_client),
    auth=Depends(require_api_key),
):
    return await handle_gemini_generate_content(model_path, req, client, stream=False)


@router.post("/v1beta/{model_path:path}:streamGenerateContent")
async def stream_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient = Depends(get_client),
    auth=Depends(require_api_key),
):
    return await handle_gemini_generate_content(model_path, req, client, stream=True)

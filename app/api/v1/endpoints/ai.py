from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.core.config import settings
from app.schemas.chat import ChatRequest
from rag.vector_store import query_documents
import httpx
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/chat")
async def chat(
    request: ChatRequest,
    current_user: User = Depends(get_current_user)
):
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise HTTPException(status_code=500, detail="API Key not configured")

    TIMEOUT = 25

    async def generate():
        try:
            # 真实 RAG 来源溯源：检索知识库，把命中文档作为上下文并随 SSE 推送来源
            sources = []
            context = None
            if request.return_sources:
                try:
                    scored = await asyncio.to_thread(query_documents, request.prompt, 3)
                    sources = [
                        {"source": d["document"][:200], "score": d["score"]}
                        for d in scored
                    ]
                    if scored:
                        context = "\n".join(d["document"] for d in scored)
                except Exception as e:
                    logger.warning("chat 检索来源失败，忽略来源: %s", e)
                    sources = []

            if context:
                final_prompt = f"基于以下文档回答问题：\n{context}\n\n问题：{request.prompt}"
            else:
                final_prompt = request.prompt

            # 先推送来源溯源事件，再推送内容
            if sources:
                yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"

            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    "https://api.deepseek.com/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": final_prompt}],
                        "stream": True
                    }
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue
                    yield "data: [DONE]\n\n"

        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'error': '请求超时，请稍后重试'})}\n\n"
        except httpx.HTTPStatusError as e:
            yield f"data: {json.dumps({'error': f'API错误: {e.response.status_code}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'服务器内部错误: {str(e)}'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

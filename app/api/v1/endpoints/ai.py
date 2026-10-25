from fastapi import APIRouter, Depends, HTTPException, Request
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
    http_request: Request,
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
                    # 与 /rag/ask 同一阈值门控：低于置信度下限的文档不作为上下文注入。
                    # 原实现无条件注入 top3，"你好"也会被塞进相似度 0.33 的无关文档，
                    # 模型只能就着无关资料作答。来源列表仍完整返回（含分数）供追溯。
                    qualified = [
                        d for d in scored
                        if d["score"] >= settings.RAG_SIMILARITY_THRESHOLD
                    ]
                    if qualified:
                        context = "\n".join(d["document"] for d in qualified)
                except Exception as e:
                    logger.warning("chat 检索来源失败，忽略来源: %s", e)
                    sources = []

            if context:
                final_prompt = f"基于以下文档回答问题：\n{context}\n\n问题：{request.prompt}"
            else:
                final_prompt = request.prompt

            # 先推送来源溯源事件，再推送内容
            if sources:
                # 检索阶段（约 1s）客户端可能已关页面：断开就别再发起上游请求
                if await http_request.is_disconnected():
                    logger.info("chat 客户端在检索阶段已断开，放弃生成")
                    return
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
                        # 每个 chunk yield 前检测断开：客户端关页面即停，不再白烧上游 token。
                        # 上游流的关闭由 async with 退出时的 response.aclose() 自动完成，
                        # 无需（也不能）在 break 后手动关——手动 stream=True 才需要 finally: aclose。
                        if await http_request.is_disconnected():
                            logger.info("chat 客户端已断开，中止生成，上游流随 async with 退出自动关闭")
                            return
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

        # httpx 的超时抛的是 httpx.TimeoutException（ReadTimeout/ConnectTimeout 等），
        # 它挂在 TransportError 下，**不是** asyncio.TimeoutError 的子类。
        # 原实现只写 asyncio.TimeoutError，于是 25s 空闲超时会掉进下面的 except Exception，
        # 客户端收到"服务器内部错误: The read operation timed out"而不是"请求超时"。
        except (asyncio.TimeoutError, httpx.TimeoutException) as e:
            logger.warning("chat 流式请求超时: %s", e)
            yield f"data: {json.dumps({'error': '请求超时，请稍后重试'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except httpx.HTTPStatusError as e:
            logger.warning("chat 上游返回 %s: %s", e.response.status_code, e.response.text[:200])
            yield f"data: {json.dumps({'error': f'API错误: {e.response.status_code}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            # 只把异常类型给客户端，原始信息（可能含连接串等内部细节）只写日志
            logger.warning("chat 生成失败: %s: %s", type(e).__name__, e)
            yield f"data: {json.dumps({'error': f'服务器内部错误: {type(e).__name__}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # 取消路径兜底检测：客户端断开时 Starlette 的 StreamingResponse 内部
            # listen_for_disconnect 会取消流响应任务，生成器在 await 点退出——
            # 上面的轮询点（循环开头）来不及执行，断开日志会丢。本地 uvicorn 实测：
            # 断开后 finally 里 is_disconnected() 返回 True，这里补一次检测，
            # 保证"断开即停"有日志可查。finally 里不能 return / raise，
            # 否则会吞掉正在传播的 CancelledError。
            try:
                if await http_request.is_disconnected():
                    logger.info("chat 客户端已断开（取消路径），上游流已随 async with 退出自动关闭")
            except BaseException:
                pass  # 检测失败不影响原始异常的传播

    return StreamingResponse(generate(), media_type="text/event-stream")

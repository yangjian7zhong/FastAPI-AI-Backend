import asyncio
import logging, time

import httpx

from app.core.config import settings
from rag.vector_store import query_documents

# 相似度阈值：归一化相似度低于 0.6 视为无相关文档，降级纯模型回答
SIMILARITY_THRESHOLD = 0.6


async def ask_with_rag(question: str, n_results: int = 3) -> dict:
    retrieve_started_at = time.perf_counter()
    try:
        scored_docs = await asyncio.to_thread(query_documents, question, n_results)
    except Exception as e:
        logging.warning("RAG 检索失败，降级纯模型回答: %s", e)
        scored_docs = []

    # 阈值过滤：只保留相似度 >= 0.6 的文档作为上下文
    qualified = [d for d in scored_docs if d["score"] >= SIMILARITY_THRESHOLD]
    if qualified:
        context = "\n---\n".join(d["document"] for d in qualified)
    else:
        context = "未找到相关资料。"

    prompt = f"""你是一个知识库问答助手。请根据以下资料回答用户的问题。
如果资料中没有相关信息，请直接说"未找到相关资料"。

### 资料 ###
{context}

### 问题 ###
{question}

### 回答 ###"""

    deepseek_started_at = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
    deepseek_completed_at = time.perf_counter()

    logging.info("rag_ask_timing retrieval=%.3fs external=%.3fs post=%.3fs", deepseek_started_at - retrieve_started_at, deepseek_completed_at - deepseek_started_at, time.perf_counter() - deepseek_completed_at)
    return {
        "answer": answer,
        "sources": scored_docs,
        "context_used": context,
        "threshold": SIMILARITY_THRESHOLD,
        "used_threshold_fallback": bool(scored_docs) and not qualified,
    }

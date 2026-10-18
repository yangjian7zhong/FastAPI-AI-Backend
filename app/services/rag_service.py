import asyncio
import logging
import time
from collections import OrderedDict

import httpx
from fastapi import HTTPException

from app.core.config import settings
from rag.vector_store import query_documents

# 相似度阈值：归一化相似度低于该值视为低置信度，降级为纯模型回答。
# 取值来自 config（env: RAG_SIMILARITY_THRESHOLD，默认 0.45），
# 标定依据见 config.py 注释：0.6 会误杀本语料上的相关文档。
SIMILARITY_THRESHOLD = settings.RAG_SIMILARITY_THRESHOLD

DEEPSEEK_TIMEOUT_SECONDS = 60.0
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"


# ==================== 问答缓存 ====================
# 端点的 bypass_cache 参数原本是死代码：算完 admin 判断就丢掉了，没有缓存可跳。
# 这里补上真实缓存，让那个参数有了语义。
# 作用域是单进程内存（多 worker 各自一份），TTL 内同一问题直接复用答案。

QA_CACHE_TTL_SECONDS = 300
QA_CACHE_MAX_ENTRIES = 128

_qa_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()


def _cache_key(question: str, n_results: int) -> tuple:
    return (question.strip(), n_results)


def get_cached_answer(question: str, n_results: int) -> dict | None:
    """命中且未过期则返回答案副本，否则返回 None（过期的顺手清掉）。"""
    key = _cache_key(question, n_results)
    hit = _qa_cache.get(key)
    if hit is None:
        return None
    created_at, payload = hit
    if time.time() - created_at > QA_CACHE_TTL_SECONDS:
        _qa_cache.pop(key, None)
        return None
    _qa_cache.move_to_end(key)          # LRU：命中的挪到队尾
    return dict(payload)


def put_cached_answer(question: str, n_results: int, payload: dict) -> None:
    """写入缓存，超出容量时淘汰最久未使用的条目。"""
    key = _cache_key(question, n_results)
    _qa_cache[key] = (time.time(), dict(payload))
    _qa_cache.move_to_end(key)
    while len(_qa_cache) > QA_CACHE_MAX_ENTRIES:
        _qa_cache.popitem(last=False)


def clear_answer_cache() -> int:
    """清空缓存，返回清掉的条目数（重建知识库后可调用）。"""
    n = len(_qa_cache)
    _qa_cache.clear()
    return n


async def ask_with_rag(question: str, n_results: int = 3) -> dict:
    retrieve_started_at = time.perf_counter()
    try:
        scored_docs = await asyncio.to_thread(query_documents, question, n_results)
    except Exception as e:
        logging.warning("RAG 检索失败，降级纯模型回答: %s", e)
        scored_docs = []

    # 阈值过滤：只保留相似度 >= 阈值的文档作为上下文
    qualified = [d for d in scored_docs if d["score"] >= SIMILARITY_THRESHOLD]
    if qualified:
        context = "\n---\n".join(d["document"] for d in qualified)
        prompt = f"""你是一个知识库问答助手。请根据以下资料回答用户的问题。
如果资料中没有相关信息，请明确说明"资料中未提及"，不要编造。

### 资料 ###
{context}

### 问题 ###
{question}

### 回答 ###"""
    else:
        # 低置信度降级：不使用知识库资料，直接凭模型自身知识回答。
        # 原实现在这里把 context 换成"未找到相关资料。"、并让模型看到这句话，
        # 结果是模型永远回答"未找到相关资料"——降级等于拒答，RAG 形同虚设。
        # 现在：照常调用模型，只是不注入检索上下文；来源仍随响应返回，可追溯。
        context = None
        prompt = f"""你是一个乐于助人的 AI 助手。请直接根据你自己的知识回答用户的问题。
回答应简洁、准确；不确定时如实说明，不要编造具体事实。

### 问题 ###
{question}

### 回答 ###"""

    deepseek_started_at = time.perf_counter()

    # 原实现这里没有 try/except：raise_for_status 抛出的 HTTPStatusError 会一路
    # 冒泡到 main.py 的全局异常处理器，变成 500，而且 detail 里带的是原始异常字符串
    # （既语义不对，又有信息泄露）。这里按状态分流成本层可以讲清楚的两类错误，
    # 只把"上游不可用"这个结论给客户端，原始异常只写日志。
    try:
        async with httpx.AsyncClient(timeout=DEEPSEEK_TIMEOUT_SECONDS) as client:
            response = await client.post(
                DEEPSEEK_CHAT_URL,
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
    except httpx.TimeoutException:
        logging.warning(
            "rag_ask 生成超时（>%ss） retrieval=%.3fs",
            DEEPSEEK_TIMEOUT_SECONDS, deepseek_started_at - retrieve_started_at,
        )
        raise HTTPException(status_code=504, detail="生成服务超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        logging.warning(
            "rag_ask 上游返回 %s: %s",
            e.response.status_code, e.response.text[:200],
        )
        raise HTTPException(status_code=502, detail="生成服务暂时不可用，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logging.warning("rag_ask 生成失败: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail="生成服务暂时不可用，请稍后重试")

    deepseek_completed_at = time.perf_counter()

    logging.info(
        "rag_ask_timing retrieval=%.3fs external=%.3fs post=%.3fs",
        deepseek_started_at - retrieve_started_at,
        deepseek_completed_at - deepseek_started_at,
        time.perf_counter() - deepseek_completed_at,
    )
    return {
        "answer": answer,
        "sources": scored_docs,
        "context_used": context,
        "threshold": SIMILARITY_THRESHOLD,
        "used_threshold_fallback": bool(scored_docs) and not qualified,
    }

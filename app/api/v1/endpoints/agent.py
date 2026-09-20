from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agent.core import run_agent
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User

router = APIRouter()


class AgentRequest(BaseModel):
    query: str


@router.post("/agent")
async def agent_endpoint(req: AgentRequest, current_user: User = Depends(get_current_user)):
    """ReAct agent 入口。

    原实现只回 query/answer/steps，把 run_agent 已经算好的出口信息全丢了，
    调用方拿到一段"Action: xxx / Action Input: {...}"原文也分不出这是模型正常的
    最终答案，还是熔断/跑满轮数后的兜底。这里把出口信息一并透出。
    """
    result = await run_agent(req.query)
    return {
        "query": req.query,
        "answer": result["output"],
        "steps": result["intermediate_steps"],
        "exit_reason": result["exit_reason"],            # final_answer / circuit_breaker / max_iterations / llm_error
        "iterations_used": result["iterations_used"],
        "max_iterations": result["max_iterations"],
        "circuit_breaker_tripped": result["circuit_breaker_tripped"],
        "total_tokens_estimated": result["total_tokens_estimated"],
        "degraded": result["exit_reason"] != "final_answer",   # 一行就能判断答案是否可信
        "error": result["error"],
    }

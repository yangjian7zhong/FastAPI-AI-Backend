from fastapi import APIRouter, Depends, HTTPException
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.agent.core import run_agent
from pydantic import BaseModel

router = APIRouter()

class AgentRequest(BaseModel):
    query: str

@router.post("/agent")
async def agent_endpoint(req: AgentRequest, current_user: User = Depends(get_current_user)):
    result = await run_agent(req.query)
    return {
        "query": req.query,
        "answer": result["output"],
        "steps": result["intermediate_steps"]
    }
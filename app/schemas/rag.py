from pydantic import BaseModel, Field


class RagRetrieveRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(3, ge=1, le=20)


class RagAskRequest(RagRetrieveRequest):
    bypass_cache: bool = False

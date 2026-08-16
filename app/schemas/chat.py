from pydantic import BaseModel

class ChatRequest(BaseModel):
    prompt: str
    return_sources: bool = True  # 控制是否返回检索来源，默认开启
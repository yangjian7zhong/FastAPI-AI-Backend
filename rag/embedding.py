import httpx
from app.core.config import settings

async def get_embedding(text: str) -> list:
    """调用硅基流动 API，将文本转为向量[reference:10]"""
    url = "https://api.siliconflow.cn/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "BAAI/bge-large-zh-v1.5",  # 中文 embedding 模型
        "input": text
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

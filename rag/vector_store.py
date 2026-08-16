import logging
import hashlib
from pathlib import Path
import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.config import Settings as ChromaSettings
import asyncio
import httpx
from app.core.config import settings  # 确保能从 .env 读取 SILICONFLOW_API_KEY

logger = logging.getLogger(__name__)

# 1. 自定义嵌入函数类（SiliconFlow 专用）
class SiliconFlowEmbeddingFunction(EmbeddingFunction):
    def __init__(self, api_key: str, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = "https://api.siliconflow.cn/v1/embeddings"

    def __call__(self, input: Documents) -> Embeddings:
        if not self.api_key:
            raise RuntimeError("SILICONFLOW_API_KEY 未配置，无法调用嵌入服务（RAG 检索降级）")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "input": list(input)
        }
        async def fetch_embeddings() -> Embeddings:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.base_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return [item["embedding"] for item in data["data"]]

        response = asyncio.run(fetch_embeddings())
        return response

# 2. 初始化 ChromaDB（使用自定义嵌入函数，余弦相似度检索）
siliconflow_ef = SiliconFlowEmbeddingFunction(api_key=settings.SILICONFLOW_API_KEY)

client = chromadb.PersistentClient(path="./chroma_db", settings=ChromaSettings(anonymized_telemetry=False, chroma_product_telemetry_impl="app.core.chroma_telemetry.NoopProductTelemetry"))

# 余弦空间：hnsw:space=cosine。
# 注意：get_or_create_collection 对已存在的旧库会覆盖其 metadata（造成"假 cosine"误判），
# 因此这里先 get、不存在才 create，并用自有标记 dsh_schema=2 记录真实 schema。
try:
    collection = client.get_collection("knowledge_base", embedding_function=siliconflow_ef)
    logger.info("加载已有 collection: knowledge_base (metadata=%s)", collection.metadata or {})
except Exception:
    collection = client.create_collection(
        name="knowledge_base",
        embedding_function=siliconflow_ef,
        metadata={"hnsw:space": "cosine", "dsh_schema": "2"},
    )

# 3. 工具函数
def add_documents(ids: list, documents: list, metadatas: list = None):
    """添加文档到向量库"""
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas or [{}] * len(documents)
    )


def _cosine_similarity_from_distance(distance: float) -> float:
    """余弦距离 → 归一化相似度：
    余弦距离 ∈ [0, 2]，similarity = 1 - distance，clamp 到 [0, 1]。
    兼容旧 L2 空间：距离越小越相似，统一映射到 [0,1] 便于阈值判断。"""
    sim = 1.0 - float(distance)
    return max(0.0, min(1.0, sim))


def query_documents(query: str, n_results: int = 3) -> list:
    """检索最相似的文档，返回 [{"document": ..., "score": 归一化相似度(0~1)}, ...]。
    向量库/嵌入服务不可用时返回 []（由上层降级纯模型回答），不抛异常。"""
    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "distances"],
        )
    except Exception as e:
        logger.warning("RAG 检索降级（向量库不可用）: %s", e)
        return []

    docs = (results.get("documents") or [[]])[0] or []
    dists = (results.get("distances") or [[]])[0] or []
    scored = []
    for doc, dist in zip(docs, dists):
        scored.append({
            "document": doc,
            "score": round(_cosine_similarity_from_distance(dist), 4),
        })
    return scored


# ========== 自动迁移：确保 collection 为 cosine 空间，必要时重建并灌库 ==========

def _ingest_documents(col) -> int:
    """把 documents/ 下的 txt/md 分块灌入指定 collection（增量：只补缺失的片段），
    并清理过期片段（源文件删除或内容改写后残留的旧 chunk）。
    返回本次新增片段数。与 scripts/ingest_documents.py 逻辑保持一致。"""
    root = Path(__file__).resolve().parent.parent
    docs_dir = root / "documents"
    files = []
    for pat in ("*.txt", "*.md"):
        files.extend(docs_dir.glob(pat))
        files.extend(docs_dir.glob(f"**/{pat}"))
    files = sorted(set(files))
    if not files:
        logger.warning("documents/ 目录为空，跳过自动灌库")
        return 0

    # 生成期望片段（id 由 源文件+序号+内容 决定，内容变更则 id 变化）
    expected = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(docs_dir).as_posix()
        for i, chunk in enumerate(text.split("\n\n")):
            chunk = chunk.strip()
            if not chunk:
                continue
            doc_id = hashlib.md5(f"{rel}:{i}:{chunk[:80]}".encode()).hexdigest()
            expected.append((doc_id, chunk, {"source": rel, "chunk": i}))
    expected_ids = {item[0] for item in expected}

    # 清理过期片段：集合中不在期望 id 集合里的旧数据（文件删除/内容改写后残留）
    try:
        all_data = col.get()
        stale_ids = [id_ for id_ in all_data["ids"] if id_ not in expected_ids]
        if stale_ids:
            col.delete(ids=stale_ids)
            logger.info("已清理 %d 个过期片段（源文件删除或内容变更）", len(stale_ids))
    except Exception as e:
        logger.warning("过期片段清理失败: %s", e)

    # 增量补灌：只 add 缺失的片段
    try:
        existing_ids = set(col.get()["ids"])
    except Exception:
        existing_ids = set()
    to_add = [item for item in expected if item[0] not in existing_ids]

    if to_add:
        col.add(
            ids=[item[0] for item in to_add],
            documents=[item[1] for item in to_add],
            metadatas=[item[2] for item in to_add],
        )
        logger.info("已入库 %d 个新片段（来源 %d 个文件）", len(to_add), len(files))
    else:
        logger.info("无新片段需要入库（知识库已是最新）")
    return len(to_add)


def rebuild_collection_if_needed() -> dict:
    """启动/手动触发时的自检：
    - collection 已是 cosine 空间 → 跳过
    - 旧空间（L2 等）→ 删除重建为 cosine 并自动灌库
    - 不存在 → 新建 cosine 并自动灌库
    返回 {"rebuilt": bool, "count": int, "reason": str}
    """
    global collection
    try:
        existing = client.get_collection("knowledge_base", embedding_function=siliconflow_ef)
        meta = existing.metadata or {}
        if meta.get("dsh_schema") == "2" and meta.get("hnsw:space") == "cosine":
            # 每次启动做一次增量补灌（documents/ 新增的文档自动入库，幂等）
            added = _ingest_documents(collection)
            if added > 0:
                return {"rebuilt": False, "count": collection.count(), "reason": "incremental_ingested"}
            return {"rebuilt": False, "count": collection.count(), "reason": "already_cosine"}
        logger.warning("检测到旧/未知 schema collection（metadata=%s），删除并重建为 cosine", meta)
        client.delete_collection("knowledge_base")
    except Exception as e:
        logger.info("未找到现有 collection，将新建 cosine 集合: %s", e)

    collection = client.create_collection(
        name="knowledge_base",
        embedding_function=siliconflow_ef,
        metadata={"hnsw:space": "cosine", "dsh_schema": "2"},
    )
    count = _ingest_documents(collection)
    return {"rebuilt": True, "count": count, "reason": "rebuilt_to_cosine"}

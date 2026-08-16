"""使用本地模型灌入文档到 chroma_db_local 目录。"""
import hashlib
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings as ChromaSettings

DOCS_DIR = ROOT / "documents"
LOCAL_CHROMA_PATH = ROOT / "chroma_db_local"

def main():
    model = SentenceTransformer('paraphrase-MiniLM-L3-v2')
    client = chromadb.PersistentClient(
        path=str(LOCAL_CHROMA_PATH),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection("local_docs")
    if collection.count() > 0:
        print(f"集合已有 {collection.count()} 条，跳过。如需重建请删除 {LOCAL_CHROMA_PATH}")
        return

    files = list(DOCS_DIR.glob("**/*.txt")) + list(DOCS_DIR.glob("**/*.md"))
    if not files:
        raise SystemExit(f"未找到文档，请放入 {DOCS_DIR}")

    ids, docs, metas = [], [], []
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(DOCS_DIR).as_posix()
        for i, chunk in enumerate(text.split("\n\n")):
            chunk = chunk.strip()
            if not chunk:
                continue
            doc_id = hashlib.md5(f"{rel}:{i}:{chunk[:80]}".encode()).hexdigest()
            ids.append(doc_id)
            docs.append(chunk)
            metas.append({"source": rel, "chunk": i})

    embeddings = model.encode(docs).tolist()
    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    print(f"已入库 {len(docs)} 个片段，来源 {len(files)} 个文件。")

if __name__ == "__main__":
    main()
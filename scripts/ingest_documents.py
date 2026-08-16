"""将 documents/ 下的文本灌入 Chroma。在项目根目录执行: python scripts/ingest_documents.py"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.vector_store import add_documents, collection  # noqa: E402

DOCS_DIR = ROOT / "documents"


def main():
    patterns = ("*.txt", "*.md")
    files: list[Path] = []
    for pat in patterns:
        files.extend(DOCS_DIR.glob(pat))
        files.extend(DOCS_DIR.glob(f"**/{pat}"))
    files = sorted(set(files))
    if not files:
        raise SystemExit(f"未找到文档，请放入 {DOCS_DIR}")

    existing = collection.count()
    if existing > 0:
        print(f"集合已有 {existing} 条，跳过入库。若要重建请删除 chroma_db/ 后重跑。")
        return

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

    add_documents(ids, docs, metas)
    print(f"已入库 {len(docs)} 个片段，来源 {len(files)} 个文件。")


if __name__ == "__main__":
    main()
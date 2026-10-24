# FastAPI AI 后端生产镜像
#
# 说明：仓库里另有 Dockerfile.streamlit.bak，那是给 Streamlit 演示前端用的（CMD 是
#       streamlit run streamlit_app.py）。Railway 上跑 API 请用本文件，或直接用
#       Procfile / nixpacks.toml 里的 uvicorn 启动方式。
#
# 分层顺序：基础镜像 → 系统依赖 → Python 依赖 → 代码
# 依赖单独一层，改业务代码不会触发重装（torch 那一层尤其慢）。

FROM python:3.11-slim

# ---------- 运行时环境 ----------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/huggingface

WORKDIR /app

# ---------- 系统依赖 ----------
# curl 只给 HEALTHCHECK 用；装完立刻清 apt 列表，避免镜像白白变大
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# ---------- 依赖层 ----------
COPY requirements.txt .

RUN pip install --root-user-action=ignore --upgrade pip setuptools wheel packaging

# torch 走官方 CPU 源：默认源会拉 CUDA 版，镜像从 ~2GB 涨到 ~7GB
RUN pip install --root-user-action=ignore \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.2.2+cpu

RUN pip install --root-user-action=ignore \
    -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# ---------- 代码层 ----------
COPY . .

# ---------- 非 root ----------
# Chroma 会写 ./chroma_db、SQLite 回退会写 test.db、HF 缓存写 /opt/huggingface，
# 这三处都要让 appuser 可写，否则容器一启动就 PermissionError。
RUN mkdir -p /opt/huggingface \
 && groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser \
 && chown -R appuser:appuser /app /opt/huggingface

USER appuser

EXPOSE 8000

# ---------- 健康检查 ----------
# 根路由返回 {"msg":"FastAPI 项目已启动"}，200 即视为存活。
# start-period 给 40s：实测冷启动约 6s（import chromadb/transformers 约 12s，
# uvicorn 监听在 import 完成前就绪），40s 是留足余量，避免慢机器上被误判。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/" || exit 1

# ---------- 启动 ----------
# Railway 会注入 $PORT；本地 docker run 没注入时回落到 8000。
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

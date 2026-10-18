"""Agent 工具集。

安全约定：
- execute_python：AST 静态校验 + 子进程隔离（-I -S）+ 限时，代码长度有上限
- calculator：不用 AST，靠字符白名单 + 表达式长度/幂次上限兜住 DoS
- 所有工具都是同步函数，由 core.run_agent 通过 asyncio.to_thread 调用，避免阻塞事件循环
- 工具内部自己吞异常并返回说明文本，不把异常抛给上层
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

from app.core.config import settings
from rag.vector_store import query_documents

# ========== Python 执行器沙箱 ==========

# 仅允许导入以下标准库模块
ALLOWED_MODULES = {
    "math", "json", "random", "datetime", "statistics",
    "re", "collections", "string", "itertools", "functools",
}

# 禁止调用的内建名（反射三件套是逃逸主力，必须挡）
FORBIDDEN_BUILTINS = {
    "__import__", "eval", "exec", "open", "compile", "input", "breakpoint",
    "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "hasattr",
    "type", "object", "super", "memoryview", "classmethod", "staticmethod",
}

# 禁止访问的属性名（非 dunder 部分；dunder 由 __ 前缀规则统一拦截）
FORBIDDEN_ATTRS = {
    "system", "popen", "run", "subprocess", "eval", "exec", "open",
    "remove", "unlink", "rmdir", "mkdir", "chmod", "chown", "rename",
    "read", "write", "connect", "request",
}

MAX_CODE_LEN = 4000
EXEC_TIMEOUT_SECONDS = 1


def _check_ast(code: str):
    """静态校验，通过返回 None，否则返回错误信息。

    前三条是"列举禁词"，后两条是结构性规则——结构规则才是封逃逸的关键：
      1. import / from-import 的顶层模块必须在 ALLOWED_MODULES 内
      2. 被调用的具名函数在 FORBIDDEN_BUILTINS 内 → 拒绝
      3. 被调用/访问的属性名在 FORBIDDEN_ATTRS 内 → 拒绝
      4. 【结构】任何以 __ 开头的名字或属性 → 拒绝
         一条规则同时封死 __builtins__ / __import__ 入口和
         __class__.__bases__[0].__subclasses__() 这类 dunder 逃逸链
      5. 【结构】Call 的 func 既不是 Name 也不是 Attribute → 拒绝
         封死 __builtins__.__dict__["open"](...) 这种"下标取出可调用对象"的写法
    """
    if len(code) > MAX_CODE_LEN:
        return f"代码过长（{len(code)} > {MAX_CODE_LEN} 字符）"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    return f"禁止导入模块: {alias.name}"

        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] not in ALLOWED_MODULES:
                return f"禁止导入模块: {node.module}"

        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                return f"禁止使用双下划线名字: {node.id}"

        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return f"禁止访问双下划线属性: {node.attr}"
            if node.attr in FORBIDDEN_ATTRS:
                return f"禁止访问属性: {node.attr}"

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in FORBIDDEN_BUILTINS:
                    return f"禁止调用: {func.id}"
                if func.id.startswith("__"):
                    return f"禁止调用双下划线名字: {func.id}"
            elif isinstance(func, ast.Attribute):
                if func.attr.startswith("__"):
                    return f"禁止调用双下划线方法: {func.attr}"
                if func.attr in FORBIDDEN_ATTRS:
                    return f"禁止调用: {func.attr}"
            else:
                return "禁止调用非具名可调用对象（只允许 name(...) 或 obj.method(...)）"

    return None


def execute_python(code: str) -> str:
    """安全执行 Python 代码（AST 校验 + 子进程隔离 + 限时）"""
    err = _check_ast(code)
    if err:
        return f"安全策略拒绝: {err}"

    fname = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(code)
            fname = f.name
        # -I 隔离模式：忽略用户环境（PYTHONPATH 等）；-S 不加载 site 包，配合白名单
        # 用 sys.executable 而不是裸 "python"，避免容器/PATH 里解析到别的解释器
        result = subprocess.run(
            [sys.executable, "-I", "-S", fname],
            capture_output=True, text=True, timeout=EXEC_TIMEOUT_SECONDS,
        )
        return (result.stdout or result.stderr or "").strip() or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"代码执行超时（上限 {EXEC_TIMEOUT_SECONDS}s）"
    except Exception as e:
        return f"代码执行失败: {type(e).__name__}: {e}"
    finally:
        if fname:
            try:
                os.unlink(fname)
            except OSError:
                pass


# ========== 业务工具 ==========


def rag_search(query: str) -> str:
    """真实 RAG 检索：复用 rag/vector_store.py 的 Chroma 余弦检索。

    与 /rag/ask 走同一套检索实现（余弦空间 + 归一化相似度）。
    这里不套置信度阈值（默认 0.45，env 可调），把带分数的前 3 条原文交给模型自己判断。
    """
    try:
        docs = query_documents(query, n_results=3)
    except Exception as e:
        return f"检索失败: {type(e).__name__}: {e}"

    if not docs:
        return f"未检索到与「{query}」相关的文档（向量库不可用或知识库为空）。"

    return "\n\n".join(
        f"[{i + 1}] 相似度 {d.get('score', 0):.2f}\n{d.get('document', '')}"
        for i, d in enumerate(docs)
    )


def web_search(query: str) -> str:
    """使用 Tavily 搜索；未配置 Key 或调用失败时返回降级说明，不抛异常。

    注意：settings 的读取本身也放在 try 内——原实现把 `if settings.TAVILY_API_KEY`
    写在 try 之外，配置项缺失时直接 AttributeError 冒泡成 500。
    """
    try:
        api_key = settings.TAVILY_API_KEY
        if not api_key:
            return "未配置 TAVILY_API_KEY，web_search 暂不可用；请改用 rag_search 或直接回答。"

        import tavily  # 延迟导入：未配置 Key 时不引入额外依赖开销
        client = tavily.TavilyClient(api_key=api_key)
        result = client.search(query, max_results=3)
        items = (result or {}).get("results", [])
        if not items:
            return f"未搜索到与「{query}」相关的结果。"
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        return f"搜索失败: {type(e).__name__}: {e}"


MAX_EXPR_LEN = 120
MAX_POW_COUNT = 1
MAX_POW_EXPONENT = 1000


def calculator(expression: str) -> str:
    """计算数学表达式（字符白名单，不走 AST）。

    两道规模限制是必须的：原实现只挡字符，`9**9**9` 完全合法，
    而 Python 把幂运算解析成右结合 `9 ** (9 ** 9)`，eval 会去算一个约 3.7 亿位的整数，
    直接把进程算死。这里限制"最多一次幂运算 + 指数上限"，并给表达式长度封顶。
    """
    expr = (expression or "").strip()
    if not expr:
        return "表达式为空"
    if len(expr) > MAX_EXPR_LEN:
        return f"表达式过长（{len(expr)} > {MAX_EXPR_LEN} 字符）"

    allowed = {"+", "-", "*", "/", "%", "(", ")", ".", " "}
    if not all(c in allowed or c.isdigit() for c in expr):
        return "表达式包含非法字符"

    if len(re.findall(r"\*\*", expr)) > MAX_POW_COUNT:
        return f"表达式含多次幂运算（上限 {MAX_POW_COUNT} 次，避免嵌套幂把进程算死）"
    for m in re.finditer(r"\*\*\s*(\d+)", expr):
        if int(m.group(1)) > MAX_POW_EXPONENT:
            return f"幂次过大（上限 {MAX_POW_EXPONENT}）"

    try:
        # 第二个参数清空 __builtins__：字符白名单已挡掉所有名字，这里再收一层
        value = eval(expr, {"__builtins__": {}}, {})
        return f"计算结果: {value}"
    except Exception as e:
        return f"计算错误: {type(e).__name__}: {e}"


TOOLS = {
    "rag_search": rag_search,
    "web_search": web_search,
    "execute_python": execute_python,
    "calculator": calculator,
}
TOOL_DESCRIPTIONS = {
    "rag_search": "用于从知识库检索信息（真实向量检索，返回带相似度的片段）",
    "web_search": "用于搜索互联网实时信息（需配置 TAVILY_API_KEY）",
    "execute_python": "用于执行Python代码（AST校验 + 子进程隔离 + 1秒超时）",
    "calculator": "用于数学计算（仅支持 +-*/% 与幂运算）",
}

import httpx
import subprocess
import tempfile
import os
import json
import ast
from app.core.config import settings

# Python 执行器模块白名单：仅允许导入以下标准库模块
ALLOWED_MODULES = {
    "math", "json", "random", "datetime", "statistics",
    "re", "collections", "string", "itertools", "functools",
}
# 禁止调用的内建名与危险属性
FORBIDDEN_BUILTINS = {"__import__", "eval", "exec", "open", "compile", "input", "breakpoint", "globals", "locals", "vars"}
FORBIDDEN_ATTRS = {
    "system", "popen", "run", "subprocess", "exec", "eval", "open",
    "remove", "unlink", "rmdir", "mkdir", "chmod", "chown", "rename",
    "read", "write", "connect", "request", "getattr", "setattr", "delattr",
}


def _check_ast(code: str):
    """白名单静态校验：返回错误信息或 None"""
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
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
                return f"禁止调用: {node.func.id}"
            if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_ATTRS:
                return f"禁止调用: {node.func.attr}"
    return None


def rag_search(query: str) -> str:
    """模拟RAG检索（可替换为真实向量库）"""
    return f"关于 '{query}' 的检索结果：\n- 相关文档片段1\n- 相关文档片段2"

def web_search(query: str) -> str:
    """使用Tavily搜索（如果没Key则返回模拟数据）"""
    if settings.TAVILY_API_KEY:
        try:
            import tavily
            client = tavily.TavilyClient(api_key=settings.TAVILY_API_KEY)
            result = client.search(query, max_results=3)
            return json.dumps(result.get("results", []), ensure_ascii=False)
        except Exception as e:
            return f"搜索失败: {e}"
    return f"模拟搜索结果（未配置API Key）：关于 '{query}' 的搜索结果"

def execute_python(code: str) -> str:
    """安全执行Python代码（模块白名单 + 限时1秒 + 隔离模式）"""
    err = _check_ast(code)
    if err:
        return f"安全策略拒绝: {err}"
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(code.encode())
            fname = f.name
        # -I 隔离模式：忽略用户环境（PYTHONPATH 等）；-S 不加载 site 包，配合白名单
        result = subprocess.run(["python", "-I", "-S", fname], capture_output=True, text=True, timeout=1)
        return result.stdout or result.stderr
    except subprocess.TimeoutExpired:
        return "代码执行超时"
    finally:
        os.unlink(fname)

def calculator(expression: str) -> str:
    """计算数学表达式"""
    try:
        allowed = {"+", "-", "*", "/", "(", ")", ".", " "}
        if not all(c in allowed or c.isdigit() for c in expression):
            return "表达式包含非法字符"
        return f"计算结果: {eval(expression)}"
    except Exception as e:
        return f"计算错误: {e}"

TOOLS = {
    "rag_search": rag_search,
    "web_search": web_search,
    "execute_python": execute_python,
    "calculator": calculator
}
TOOL_DESCRIPTIONS = {
    "rag_search": "用于从知识库检索信息",
    "web_search": "用于搜索互联网实时信息",
    "execute_python": "用于执行Python代码（模块白名单沙箱）",
    "calculator": "用于数学计算"
}

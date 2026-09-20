"""ReAct agent 的 system prompt 构造。

单一职责：把「可用工具列表 + 输出格式约定」拼成 system prompt。

工具列表由调用方传入 tools.TOOL_DESCRIPTIONS 生成——原实现这里是另一份手写的
中文工具名（RAG检索/网络搜索/…），和 tools.py 里的注册名（rag_search/web_search/…）
对不上，而且 core.py 自己又内联拼了第三份，导致这个文件根本没人引用。
现在只留这一处生成逻辑，避免三份描述各说各话。
"""

# 输出格式约定：模型必须二选一，解析端（core.py）的正则与这里严格对应
OUTPUT_FORMAT = """请严格按照以下两种格式之一输出：

格式一（需要调用工具时）：
Thought: 你的思考过程
Action: 工具名称
Action Input: 工具输入（JSON 格式）

格式二（可以给出答案时）：
Thought: 我有了答案
Final Answer: 最终回答

注意：
- 行动名必须是上面列出的工具名之一
- 一次只调用一个工具，拿到结果后再决定下一步
- 除以上格式外不要输出其他内容"""


def build_system_prompt(tool_descriptions: dict) -> str:
    """根据工具注册表生成 system prompt。"""
    tool_lines = "\n".join(
        f"- {name}: {desc}" for name, desc in tool_descriptions.items()
    )
    return f"""你是一个可以使用工具的智能助手。可用工具：

{tool_lines}

{OUTPUT_FORMAT}"""

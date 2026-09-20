"""ReAct 风格的手写 Agent 推理循环。

每轮四步：LLM 调用 → Token 熔断检查 → 解析 → 工具执行（Observation 以 user 消息回流）。

出口有四种，用返回值里的 exit_reason 区分（原实现只返回一个布尔，跑满轮数和正常结束
在返回值上完全区分不开，且端点又把这个布尔丢掉了）：
- final_answer      模型给出了 Final Answer，正常结束
- circuit_breaker   累计估算 token 超过 MAX_TOTAL_TOKENS，熔断
- max_iterations    跑满 max_iterations 轮仍未给出答案，兜底取最后一条 assistant
- llm_error         LLM 调用本身失败（新增出口；原实现会把异常直接冒泡成 500）
"""
import asyncio
import functools
import inspect
import json
import logging
import re

import httpx

from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOLS, TOOL_DESCRIPTIONS
from app.core.config import settings

logger = logging.getLogger(__name__)

# Token 累计熔断：粗略估算累计 token 数，超过预算立即停止，防止失控调用
MAX_TOTAL_TOKENS = 4000

LLM_TIMEOUT_SECONDS = 30.0
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"

# 解析正则与 prompts.OUTPUT_FORMAT 里约定的格式严格对应
ACTION_PATTERN = re.compile(r"Action:\s*(\w+)\s*Action Input:\s*(\{.*\})", re.DOTALL)
FINAL_PATTERN = re.compile(r"Final Answer:\s*(.+)", re.DOTALL)


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文字符近似 1 token/字符，其余按 4 字符 1 token"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    other = len(text) - cjk
    return cjk + other // 4 + 1


async def _call_llm(messages: list) -> str:
    """单次 LLM 调用；失败时抛异常，由调用方决定降级方式。"""
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(
            DEEPSEEK_CHAT_URL,
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": 0,
            },
        )
        # 原实现没有这一步：DeepSeek 返回 4xx/5xx 时会走到 data["choices"] 变成 KeyError，
        # 报错信息完全看不出真正原因
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"]


def _prepare_tool_call(tool_func, input_data: dict):
    """校验 Action Input 的字段名，返回 (可用参数, 错误说明)。"""
    params = inspect.signature(tool_func).parameters
    unknown = [k for k in input_data if k not in params]
    missing = [
        name for name, p in params.items()
        if p.default is p.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        and name not in input_data
    ]
    if unknown:
        return None, f"参数名不认识：{unknown}，该工具需要：{list(params)}"
    if missing:
        return None, f"缺少必填参数：{missing}，该工具需要：{list(params)}"
    return input_data, None


async def run_agent(query: str, max_iterations: int = 5) -> dict:
    """手动实现的 Agent 推理循环（ReAct 风格）。

    - max_iterations=5：最多 5 轮，是循环上界（不是轮内判断），跑满自然退出
    - Token 累计熔断：累计估算 token 超过预算即停止
    """
    messages = [
        {"role": "system", "content": build_system_prompt(TOOL_DESCRIPTIONS)},
        {"role": "user", "content": query},
    ]

    tool_results = []
    final_answer = None
    total_tokens = _estimate_tokens(query)
    circuit_breaker_tripped = False
    exit_reason = "max_iterations"
    iterations_used = 0
    error = None

    for iteration in range(max_iterations):
        iterations_used = iteration + 1

        # ---------- ① LLM 调用 ----------
        try:
            assistant_msg = await _call_llm(messages)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            exit_reason = "llm_error"
            logger.warning("agent LLM 调用失败: %s", error)
            break
        messages.append({"role": "assistant", "content": assistant_msg})

        # ---------- ② Token 熔断检查 ----------
        total_tokens += _estimate_tokens(assistant_msg)
        if total_tokens > MAX_TOTAL_TOKENS:
            circuit_breaker_tripped = True
            exit_reason = "circuit_breaker"
            break

        # ---------- ③ 解析 ----------
        action_match = ACTION_PATTERN.search(assistant_msg)
        final_match = FINAL_PATTERN.search(assistant_msg)

        if final_match:
            final_answer = final_match.group(1).strip()
            exit_reason = "final_answer"
            break

        # ---------- ④ 工具执行 / 无 Action 兜底 ----------
        if action_match:
            tool_name = action_match.group(1).strip()
            raw_input = action_match.group(2).strip()

            try:
                input_data = json.loads(raw_input)
                if not isinstance(input_data, dict):
                    input_data = {"query": raw_input}
            except Exception:
                # 不是合法 JSON：按单参数工具兜底，语义靠下一段的字段名校验把关
                input_data = {"query": raw_input}

            tool_func = TOOLS.get(tool_name)
            if tool_func is None:
                observation = (
                    f"错误：未找到工具 '{tool_name}'，可用工具：{', '.join(TOOLS)}"
                )
            else:
                call_args, arg_error = _prepare_tool_call(tool_func, input_data)
                if arg_error:
                    observation = f"错误：{arg_error}"
                else:
                    # 工具都是同步阻塞函数，统一丢线程池——原实现直接调用，
                    # 像 calculator 这种会算很久的工具会把事件循环一起卡住。
                    # 异常一律转成 Observation 回流给模型，不再冒泡成 500。
                    try:
                        result = await asyncio.to_thread(
                            functools.partial(tool_func, **call_args)
                        )
                    except Exception as e:
                        result = f"工具执行异常: {type(e).__name__}: {e}"
                    result_text = str(result)
                    tool_results.append({"tool": tool_name, "result": result_text})
                    total_tokens += _estimate_tokens(result_text)
                    observation = f"工具执行结果：{result_text}"

            messages.append({
                "role": "user",
                "content": f"{observation}\n请根据结果继续思考并给出最终答案。",
            })
        else:
            # 既没有 Action 也没有 Final Answer：强制要求下一轮给出答案
            messages.append({
                "role": "user",
                "content": "请直接给出最终答案（以 'Final Answer:' 开头）。",
            })

    # 跑满轮数或 LLM 失败时没有 final_answer，兜底取最后一条 assistant 消息
    if not final_answer:
        last_assistant = next(
            (m["content"] for m in reversed(messages) if m["role"] == "assistant"),
            None,
        )
        if last_assistant:
            final_answer = last_assistant

    return {
        "output": final_answer or "无法生成回答",
        "intermediate_steps": tool_results,
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "max_iterations": max_iterations,
        "iterations_used": iterations_used,
        "exit_reason": exit_reason,
        "total_tokens_estimated": total_tokens,
        "error": error,
    }

"""
LLM 对话模块 - DeepSeek API 集成（流式返回）
未配置 DEEPSEEK_API_KEY 时，使用检索结果生成本地回答，仍可回答「某秒讲了什么」。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import httpx
from nlp_retrieval import parse_target_seconds, retrieve_context

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = "deepseek-chat"
# 设为 1 时：连不上 DeepSeek 也直接报错，不降级（调试用）
_STRICT_DEEPSEEK = (os.environ.get("STRICT_DEEPSEEK") or "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def build_messages(question: str, context: str) -> list[dict]:
    """构建发送给大模型的消息"""
    system_prompt = """你是一个基于视频内容的问答助手。用户会提出与已上传视频相关的问题。
你将收到从视频中提取的上下文，包括：各时间点的人物面部表情（如开心、悲伤、惊讶等）以及画面中的文字（若有）。
请基于这些信息准确回答用户问题。若上下文中没有相关信息或为空，请友好说明并建议用户稍后重试或更换视频。

【格式要求】请使用纯中文段落与普通标点回答，不要使用 Markdown：不要出现 # 标题、**粗体**、*列表*、```代码块```、--- 分隔线等符号；需要分点时用「1.」「2.」或「一是」「二是」等中文表述。"""
    
    if context:
        user_content = f"""【检索到的视频上下文】
{context}

【用户问题】
{question}"""
    else:
        user_content = f"""【用户问题】
{question}

（当前没有检索到视频上下文，可能是尚未处理视频或暂无相关索引。请根据通用知识简要回答，并提示用户可先上传视频。）"""
    
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_local_answer(context: str, ts: Optional[float]) -> str:
    """无 API Key 时：基于 OCR+表情检索结果直接回答（流式输出）。"""
    if not (context or "").strip():
        return (
            "当前没有可用的视频分析结果（cv/result.json 为空或尚未生成）。\n"
            "请先上传视频，并等待左侧显示处理完成（OCR 就绪）后再提问。\n"
            "若已上传仍无数据，请确认后端已用正确的 CV_PYTHON 启动并完成抽帧。"
        )
    head = "根据本视频画面识别到的文字与表情（时间戳为抽帧时刻）：\n\n"
    if ts is not None:
        head = f"根据本视频在约 {int(ts)} 秒附近识别到的画面内容：\n\n"
    return head + context.strip()


async def _stream_text_as_sse(text: str):
    """将整段文本切成小块模拟流式输出，兼容前端 readSseAnswer。"""
    step = 48
    for i in range(0, len(text), step):
        chunk = text[i : i + step]
        yield "data: " + __json_msg("content", chunk) + "\n\n"
        await asyncio.sleep(0.015)
    yield "data: [DONE]\n\n"


async def stream_chat(question: str, target_seconds: Optional[float] = None):
    """
    流式调用 DeepSeek API 生成回答；未配置密钥时使用本地检索回答。
    :param question: 用户问题
    :param target_seconds: 可选，前端解析出的目标秒数，与问题内解析二选一优先用此值
    :yield: SSE 格式的文本块
    """
    ts = target_seconds if target_seconds is not None else parse_target_seconds(question)
    context = retrieve_context(question, target_seconds=ts)
    messages = build_messages(question, context)

    if not DEEPSEEK_API_KEY:
        answer = _build_local_answer(context, ts)
        async for line in _stream_text_as_sse(answer):
            yield line
        return

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async def _fallback_local(prefix: str) -> None:
        answer = _build_local_answer(context, ts)
        full = prefix + answer
        async for line in _stream_text_as_sse(full):
            yield line

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{DEEPSEEK_BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        err_s = (
                            err.decode(errors="replace")[:400] if err else ""
                        )
                        # 限流/网关故障：与「连不上」一样降级为本地检索回答（比赛/校园网常见）
                        if (
                            not _STRICT_DEEPSEEK
                            and resp.status_code in (429, 502, 503, 504)
                        ):
                            pf = (
                                f"【提示】大模型接口暂时不可用（HTTP {resp.status_code}），"
                                f"已根据本视频 OCR/表情检索结果回答：\n\n"
                            )
                            async for line in _fallback_local(pf):
                                yield line
                            return
                        yield "data: " + __json_msg(
                            "error",
                            f"API 错误: {resp.status_code} - {err_s}",
                        ) + "\n\n"
                        return

                    async for line in resp.aiter_lines():
                        if not line or not line.strip():
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                yield "data: [DONE]\n\n"
                                return
                            try:
                                obj = json.loads(data)
                                delta = obj.get("choices", [{}])[0].get(
                                    "delta", {}
                                )
                                content = delta.get("content", "")
                                if content:
                                    yield "data: " + __json_msg(
                                        "content", content
                                    ) + "\n\n"
                            except Exception:
                                pass
            except httpx.RequestError as e:
                # ConnectError: All connection attempts failed 等
                if _STRICT_DEEPSEEK:
                    yield "data: " + __json_msg("error", str(e)) + "\n\n"
                    return
                pf = (
                    "【提示】无法连接大模型服务（网络受限、防火墙或未连通 "
                    f"{DEEPSEEK_BASE_URL}）。已根据本视频 OCR/表情检索结果回答；"
                    "若需在线模型，请检查网络/代理或删除 .env 中的 DEEPSEEK_API_KEY 以始终使用本地摘要。\n\n"
                )
                async for line in _fallback_local(pf):
                    yield line
    except Exception as e:
        if not _STRICT_DEEPSEEK and isinstance(
            e, (httpx.RequestError, OSError)
        ):
            pf = (
                "【提示】调用大模型时出现异常，已降级为视频识别摘要：\n\n"
            )
            async for line in _fallback_local(pf):
                yield line
            return
        yield "data: " + __json_msg("error", str(e)) + "\n\n"


def __json_msg(typ: str, text: str) -> str:
    return json.dumps({"type": typ, "text": text}, ensure_ascii=False)

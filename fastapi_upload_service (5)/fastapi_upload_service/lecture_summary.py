"""
根据 cv/result.json 中的 OCR 文本，调用 DeepSeek 生成「文字讲义 / 总结」。
与聊天共用 DEEPSEEK_API_KEY；缓存：OCR 指纹不变时直接返回缓存。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from nlp_retrieval import CV_RESULT_PATH

APP_ROOT = Path(__file__).resolve().parent
load_dotenv(APP_ROOT.parent / ".env", encoding="utf-8-sig", override=True)
load_dotenv(APP_ROOT / ".env", encoding="utf-8-sig", override=True)

LECTURE_CACHE_PATH = APP_ROOT / "cv" / "lecture_cache.json"
_DOTENV_CANDIDATES = (
    APP_ROOT / ".env",
    APP_ROOT.parent / ".env",
)

DEFAULT_LECTURE_MODEL = "deepseek-chat"


def _parse_env_line_value(content: str, key_name: str) -> str:
    want = key_name.strip()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("\ufeff")
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != want:
            continue
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        return val.strip()
    return ""


def _read_dotenv_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except OSError:
            continue
        except UnicodeDecodeError:
            continue
    return None


def resolve_deepseek_api_key() -> str:
    k = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if k:
        return k
    for p in _DOTENV_CANDIDATES:
        text = _read_dotenv_text(p)
        if not text:
            continue
        parsed = _parse_env_line_value(text, "DEEPSEEK_API_KEY")
        if parsed:
            os.environ["DEEPSEEK_API_KEY"] = parsed
            return parsed
    return ""


def lecture_key_probe() -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "dotenv_paths": [str(p) for p in _DOTENV_CANDIDATES],
        "dotenv_exists": [p.is_file() for p in _DOTENV_CANDIDATES],
        "resolved_non_empty": bool(resolve_deepseek_api_key()),
    }


def _deepseek_base_url() -> str:
    return (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip(
        "/"
    )


def _fingerprint_from_items(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("time_offset")
        txt = str(it.get("text") or "").strip()
        parts.append(f"{t}|{txt}")
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_result_items() -> list[dict[str, Any]]:
    p = CV_RESULT_PATH
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def build_ocr_prompt_text(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for it in items:
        t = it.get("time_offset")
        txt = str(it.get("text") or "").strip()
        if not txt:
            continue
        lines.append(f"[约 {t} 秒] {txt}")
    if not lines:
        return ""
    blob = "\n".join(lines)
    max_chars = 48000
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n\n…（内容过长，已截断后半部分）"
    return blob


def _read_cache() -> dict[str, Any] | None:
    if not LECTURE_CACHE_PATH.is_file():
        return None
    try:
        return json.loads(LECTURE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(fingerprint: str, summary: str) -> None:
    LECTURE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": fingerprint,
        "summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = LECTURE_CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(LECTURE_CACHE_PATH))


def call_deepseek_summarize(ocr_text: str) -> str:
    api_key = resolve_deepseek_api_key()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    model = (
        os.environ.get("DEEPSEEK_LECTURE_MODEL")
        or DEFAULT_LECTURE_MODEL
    ).strip() or DEFAULT_LECTURE_MODEL

    system = (
        "你是教学与视频内容整理助手。用户会提供从视频多帧 OCR 得到的文字（含表情与屏幕文字），"
        "请你整理成结构清晰的中文讲义，适合复制到汇报或知识库。"
        "禁止使用 Markdown：不要出现 #、**、*、```、--- 等符号；分点请用「1.」或「一是」等中文方式。"
    )
    user_prompt = f"""请根据下面从视频各时间点截帧识别出的文字，生成一份「文字讲义 / 总结」。

要求：
1. 使用中文，分条列出核心知识点、术语与关键表述。
2. 可按时间顺序或主题归纳；需要时标注大致时刻（如「约 30 秒」）。
3. 若 OCR 有乱码、重复或无关内容，请归纳提炼，不要机械堆砌。
4. 篇幅适中，段落清晰。
5. 全文为纯文本，不要使用 Markdown 符号。

【OCR 原文】
{ocr_text}
"""

    url = f"{_deepseek_base_url()}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.45,
        "stream": False,
    }

    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()

    try:
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except Exception as e:
        raise RuntimeError(f"DeepSeek 返回格式异常: {e}") from e


def get_lecture_summary(*, refresh: bool) -> dict[str, Any]:
    """所有返回均带 provider=deepseek，便于前端区分旧版智谱后端。"""
    items = load_result_items()
    fp = _fingerprint_from_items(items)
    ocr_text = build_ocr_prompt_text(items)

    if not ocr_text.strip():
        return {
            "provider": "deepseek",
            "ok": False,
            "summary": "",
            "cached": False,
            "fingerprint": fp,
            "error": "empty_ocr",
            "message": "当前没有可用的 OCR 文本（请先完成视频处理并生成 result.json）。",
        }

    api_key = resolve_deepseek_api_key()
    if not api_key:
        tried = "、".join(str(p) for p in _DOTENV_CANDIDATES)
        return {
            "provider": "deepseek",
            "ok": False,
            "summary": "",
            "cached": False,
            "fingerprint": fp,
            "error": "no_api_key",
            "message": (
                "未读到 DEEPSEEK_API_KEY（与 /chat 相同）。已尝试："
                f"{tried}。请在 .env 中设置 DEEPSEEK_API_KEY=sk-... 后重启后端。"
            ),
        }

    if not refresh:
        cache = _read_cache()
        if cache and cache.get("fingerprint") == fp:
            s = str(cache.get("summary") or "").strip()
            if s:
                return {
                    "provider": "deepseek",
                    "ok": True,
                    "summary": s,
                    "cached": True,
                    "fingerprint": fp,
                }

    try:
        summary = call_deepseek_summarize(ocr_text)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.text[:500]
        except Exception:
            pass
        return {
            "provider": "deepseek",
            "ok": False,
            "summary": "",
            "cached": False,
            "fingerprint": fp,
            "error": "deepseek_http",
            "message": f"DeepSeek API 请求失败 ({e.response.status_code}): {detail}",
        }
    except Exception as e:
        return {
            "provider": "deepseek",
            "ok": False,
            "summary": "",
            "cached": False,
            "fingerprint": fp,
            "error": "deepseek_error",
            "message": str(e),
        }

    if not summary.strip():
        return {
            "provider": "deepseek",
            "ok": False,
            "summary": "",
            "cached": False,
            "fingerprint": fp,
            "error": "empty_summary",
            "message": "DeepSeek 返回的总结为空，请稍后重试或检查模型与 Key。",
        }

    try:
        _write_cache(fp, summary)
    except OSError:
        pass

    return {
        "provider": "deepseek",
        "ok": True,
        "summary": summary,
        "cached": False,
        "fingerprint": fp,
    }

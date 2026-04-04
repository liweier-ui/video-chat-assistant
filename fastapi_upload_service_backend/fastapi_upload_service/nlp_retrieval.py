"""
NLP 检索模块 - 供 NLP 组对接
提供 retrieve_context(question) 接口，用于根据用户问题检索相关上下文。
支持从问题中解析「第几秒 / mm:ss」，并优先返回该时间附近的 OCR+表情片段。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

APP_ROOT = Path(__file__).resolve().parent
CV_RESULT_PATH = APP_ROOT / "cv" / "result.json"
RESULT_JSON_PATH = CV_RESULT_PATH  # 供外部导入

# 时间窗口（秒）：问「某秒讲了什么」时，优先取该时刻前后若干秒的帧
TIME_FOCUS_WINDOW_SEC = 20.0


def normalize_result_json_frame_paths() -> bool:
    """
    将 cv/result.json 里每条 frame_path 强制改为相对路径 frames/frame_{time_offset}.jpg。

    典型场景：后端 zip 是别的组员在自己电脑上跑完后打包的，里面 result.json 带着对方的
    C:\\Users\\xxx\\Desktop\\... 或 D:\\公司\\... 等绝对路径。你在本机解压后路径必然无效，
    但「第几秒」的 time_offset 是对的，所以这里**只认秒数**，统一改成本项目下
    cv/frames/frame_X.jpg 对应的相对路径（前端用 /cv/frames/frame_X.jpg 访问，不依赖盘符）。

    返回是否写回了磁盘。
    """
    p = CV_RESULT_PATH
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, list) or not raw:
        return False
    changed = False
    for it in raw:
        if not isinstance(it, dict):
            continue
        ts = it.get("time_offset")
        if ts is None:
            continue
        try:
            t = int(ts)
        except (TypeError, ValueError):
            continue
        want = f"frames/frame_{t}.jpg"
        cur = str(it.get("frame_path") or "").strip()
        cur_norm = cur.replace("\\", "/")
        want_norm = want.replace("\\", "/")
        try:
            looks_absolute = Path(cur).is_absolute() if cur else False
        except OSError:
            looks_absolute = bool(cur)
        # 对方电脑上的 C:/ D:/ \\share/... 或任何不等于标准相对路径的写法，一律按秒数改成本项目 frames/
        if looks_absolute or cur_norm != want_norm:
            it["frame_path"] = want
            changed = True
    if not changed:
        return False
    tmp = p.with_suffix(".json.pathfix.tmp")
    try:
        tmp.write_text(
            json.dumps(raw, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(p))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _frame_path_belongs_to_this_project(path_str: str) -> bool:
    """
    - 新版 result.json 使用相对路径 frames/frame_0.jpg（相对 cv/），换电脑也不会出现异机盘符。
    - 旧版绝对路径必须落在当前 APP_ROOT 下，否则视为拷贝残留。
    """
    s = (path_str or "").strip()
    if not s:
        return True
    try:
        p = Path(s)
        if not p.is_absolute():
            return True
        root = str(APP_ROOT.resolve()).lower().rstrip("\\/") + os.sep
        return str(p.expanduser().resolve()).lower().startswith(root)
    except OSError:
        return False


def parse_target_seconds(question: str) -> Optional[float]:
    """
    从用户问题中提取目标时间（秒），与前端 App.tsx 的解析尽量一致。
    支持：01:15、30秒、30s、跳转类口语、40秒处讲了什么 等。
    """
    q = (question or "").strip()
    if not q:
        return None
    # mm:ss（分:秒）
    m = re.search(r"(\d{1,2}):([0-5]\d)", q)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    # 30秒 / 30s
    m = re.search(r"(\d{1,5})\s*(秒|s)\b", q, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # 跳转视频30秒处 等
    m = re.search(r"(跳转|定位|到|去)[^\d]{0,8}(\d{1,5})\s*(秒|s|处|左右)?", q)
    if m:
        return float(m.group(2))
    # 40秒处讲了什么
    m = re.search(r"(\d{1,5})\s*(秒|s)\s*(处)?\s*(讲|说|发生|内容)", q)
    if m:
        return float(m.group(1))
    return None


def retrieve_context(question: str, top_k: int = 30, target_seconds: Optional[float] = None) -> str:
    """
    根据用户问题检索相关上下文（RAG 检索）
    从 cv/result.json 读取 OCR 结果，按时间戳组织为上下文。
    优先返回包含问题关键词的片段，其余按时间顺序补充。
    
    :param question: 用户问题
    :param top_k: 返回的最相关片段数量（默认 30，覆盖更多视频内容）
    :param target_seconds: 若已知目标秒数（如前端解析后传入），优先围绕该时刻检索
    :return: 拼接后的上下文文本，供大模型生成回答
    """
    if not CV_RESULT_PATH.exists():
        return ""

    # 组员交付的 zip 里 result.json 常带异机绝对路径，读之前自动改成本机可用的相对路径
    normalize_result_json_frame_paths()

    try:
        data = json.loads(CV_RESULT_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return ""

        ts = target_seconds
        if ts is None:
            ts = parse_target_seconds(question)

        # 优先返回包含问题关键词的片段（关键词：长度>=2 的词语）
        words = [w for w in question.split() if len(w) >= 2] if question else []
        if not words:
            words = [question[i : i + 2] for i in range(max(0, len(question) - 1))]  # 中文按 2 字切分

        def _score(item: dict) -> int:
            text = item.get("text", "") or ""
            return sum(1 for k in words if k in text) if words else 0

        # 排除纯错误/无效条目，保留表情描述、文字识别等有效内容
        def _is_valid(item: dict) -> bool:
            t = (item.get("text") or "").strip()
            if not t:
                return False
            if "OCR失败" in t or "OCR识别失败" in t or "表情识别失败" in t or "无法读取帧" in t:
                return False
            if t == "未识别到文字" and "表情:" not in t:
                return False
            return True

        def _time_dist(item: dict) -> float:
            to = item.get("time_offset")
            if to is None or ts is None:
                return 99999.0
            try:
                return abs(float(to) - float(ts))
            except (TypeError, ValueError):
                return 99999.0

        valid = [
            item
            for item in data
            if isinstance(item, dict)
            and "text" in item
            and _is_valid(item)
            and _frame_path_belongs_to_this_project(str(item.get("frame_path") or ""))
        ]
        if not valid:
            return ""

        if ts is not None:
            # 先取时间窗口内的帧；若窗口内为空则退化为全序列按距离排序
            near = [it for it in valid if _time_dist(it) <= TIME_FOCUS_WINDOW_SEC]
            pool = near if near else valid
            scored = [(item, _score(item), _time_dist(item)) for item in pool]
            scored.sort(key=lambda x: (x[2], -x[1]))
            ordered = [item for item, _, _ in scored][: min(top_k, len(scored))]
        else:
            scored = [(item, _score(item)) for item in valid]
            scored.sort(key=lambda x: (-x[1], x[0].get("time_offset", 0)))
            ordered = [item for item, _ in scored][:top_k]

        parts = []
        for item in ordered:
            tso = item.get("time_offset", "?")
            parts.append(f"[{tso}秒] {item['text']}")
        return "\n\n".join(parts) if parts else ""
    except Exception:
        return ""


def _seconds_to_mmss(sec: float) -> str:
    if sec < 0 or sec != sec:  # NaN
        return "00:00"
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"


def _keywords_from_search_query(q: str) -> list[str]:
    """按空格、斜杠、逗号等切分检索词，支持中英文混合。"""
    q = (q or "").strip()
    if not q:
        return []
    parts = re.split(r"[\s/，,、；;]+", q)
    out = [p.strip() for p in parts if p.strip()]
    if not out:
        return [q]
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _score_text_for_keywords(text: str, keywords: list[str]) -> int:
    if not text or not keywords:
        return 0
    text_lower = text.lower()
    s = 0
    for kw in keywords:
        if not kw:
            continue
        if len(kw) >= 2:
            if kw.lower() in text_lower or kw in text:
                s += 1
        else:
            if kw in text:
                s += 1
    return s


def search_video_segments(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """
    按关键词在 cv/result.json 的 OCR 文本中检索，返回命中帧（供前端「视频内容搜索」）。
    每条含 time_offset、time_text(mm:ss)、title、snippet、score。
    """
    if not CV_RESULT_PATH.exists():
        return []
    normalize_result_json_frame_paths()
    keywords = _keywords_from_search_query(query)
    if not keywords:
        return []

    try:
        data = json.loads(CV_RESULT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    def _is_valid(item: dict) -> bool:
        t = (item.get("text") or "").strip()
        if not t:
            return False
        if "OCR失败" in t or "OCR识别失败" in t or "表情识别失败" in t or "无法读取帧" in t:
            return False
        return True

    scored: list[tuple[dict, int]] = []
    for item in data:
        if not isinstance(item, dict) or "text" not in item:
            continue
        if not _is_valid(item):
            continue
        if not _frame_path_belongs_to_this_project(str(item.get("frame_path") or "")):
            continue
        txt = str(item.get("text") or "")
        sc = _score_text_for_keywords(txt, keywords)
        if sc <= 0:
            continue
        scored.append((item, sc))

    scored.sort(key=lambda x: (-x[1], x[0].get("time_offset", 0)))

    out: list[dict[str, Any]] = []
    for item, sc in scored[:limit]:
        try:
            to = int(item.get("time_offset") or 0)
        except (TypeError, ValueError):
            to = 0
        txt = str(item.get("text") or "").strip()
        snippet = txt.replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        mmss = _seconds_to_mmss(float(to))
        hit_kw = next((k for k in keywords if k and (k in txt)), "")
        if hit_kw and len(hit_kw) <= 12:
            title = f"关键片段：{hit_kw}（约 {mmss}）"
        else:
            title = f"关键片段：约 {mmss}"
        out.append(
            {
                "time_offset": to,
                "time_text": mmss,
                "seconds": float(to),
                "title": title,
                "snippet": snippet,
                "score": sc,
            }
        )
    return out

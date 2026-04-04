from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# 必须从「项目根目录」（main.py 所在目录）加载 .env。
# utf-8-sig：避免 Windows 记事本保存带 BOM 导致变量名异常读不到。
# override=True：确保文件里的 Key 覆盖终端里误设的空值。
APP_ROOT = Path(__file__).resolve().parent
_ENV_FILE = APP_ROOT / ".env"
# 先加载上一层目录 .env，再加载与 main 同目录的 .env（后者覆盖前者，避免 Key 写在内层时被外层空值盖住）
load_dotenv(APP_ROOT.parent / ".env", encoding="utf-8-sig", override=True)
load_dotenv(_ENV_FILE, encoding="utf-8-sig", override=True)

_IS_WINDOWS = sys.platform == "win32"

from pydantic import BaseModel
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import database
from llm_chat import stream_chat
from nlp_retrieval import (
    CV_RESULT_PATH,
    _frame_path_belongs_to_this_project,
    normalize_result_json_frame_paths,
    retrieve_context,
    search_video_segments,
)
from lecture_summary import get_lecture_summary, lecture_key_probe

UPLOAD_DIR = APP_ROOT / "uploads"


def _in_venv_path(p: Path) -> bool:
    return ".venv" in str(p.resolve()).lower().replace("\\", "/")


def _python_for_cv_subprocess() -> str:
    """
    CV 子进程使用的 Python 解释器。

    默认：只要存在项目 .venv，就始终用 .venv\\Scripts\\python.exe（与 numpy/cv2/paddle 同一 ABI）。
    忽略 .env / 系统环境里的 CV_PYTHON，避免误指向 D:\\python 等其它版本导致秒退（用户无需改配置）。

    仅当设置环境变量 CV_FORCE_CV_PYTHON=1 时，才尊重 CV_PYTHON（给高级排障用）。
    """
    venv_py = APP_ROOT / ".venv" / "Scripts" / "python.exe"
    if not _IS_WINDOWS:
        venv_py = APP_ROOT / ".venv" / "bin" / "python"
    force = (os.environ.get("CV_FORCE_CV_PYTHON") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if venv_py.is_file() and not force:
        return str(venv_py.resolve())

    override = (os.environ.get("CV_PYTHON") or "").strip()
    if override:
        op = Path(override)
        try:
            op = op.resolve()
        except OSError:
            op = Path(override)
        if op.is_file():
            return str(op)

    if venv_py.is_file():
        return str(venv_py.resolve())

    exe = Path(sys.executable).resolve()
    if not _in_venv_path(exe):
        return str(exe)

    base_prefix = getattr(sys, "base_prefix", None) or sys.prefix
    if sys.prefix != base_prefix:
        base_py = Path(base_prefix) / ("python.exe" if _IS_WINDOWS else "python")
        if base_py.is_file() and not _in_venv_path(base_py):
            return str(base_py.resolve())

    dpy = Path(r"D:\python\python.exe")
    if dpy.is_file() and not _in_venv_path(dpy):
        return str(dpy.resolve())

    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d:
            continue
        cand = Path(d) / ("python.exe" if _IS_WINDOWS else "python")
        if not cand.is_file():
            continue
        try:
            r = cand.resolve()
        except OSError:
            continue
        if _in_venv_path(r) or r == exe:
            continue
        return str(r)

    for guess in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Python312" / "python.exe",
    ):
        try:
            if guess.is_file() and not _in_venv_path(guess):
                return str(guess.resolve())
        except OSError:
            pass

    return str(exe)


def _cv_subprocess_env() -> dict:
    """不再把 .venv 的 site-packages 塞进 PYTHONPATH 给「其它版本 Python」，避免 numpy ABI 崩溃。"""
    return os.environ.copy()


def _pick_python_for_pyvenv_repair() -> Path | None:
    """从本机找一个真实存在的 python.exe，用于修复 .venv\\pyvenv.cfg 里旧电脑上的无效路径。"""
    candidates: list[Path] = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe",
        Path(r"D:\python\python.exe"),
    ]
    bp = getattr(sys, "base_prefix", None)
    if bp:
        candidates.append(Path(bp) / "python.exe")
    try:
        candidates.append(Path(sys.executable).resolve())
    except OSError:
        pass
    for p in candidates:
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            continue
    return None


def _repair_venv_pyvenv_cfg() -> bool:
    """
    若 .venv\\pyvenv.cfg 里 home/executable 指向不存在的路径（换电脑后常见），
    子进程 .venv\\Scripts\\python.exe 会立刻退出(returncode 103)，不会写 frames/result.json。
    启动时自动改成本机存在的 Python 安装路径。
    """
    cfg_path = APP_ROOT / ".venv" / "pyvenv.cfg"
    if not cfg_path.is_file():
        return False
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return False

    old_exe: str | None = None
    old_home: str | None = None
    for line in text.splitlines():
        ls = line.strip()
        low = ls.lower()
        if low.startswith("executable") and "=" in ls:
            old_exe = ls.split("=", 1)[1].strip().strip('"')
        if low.startswith("home") and "=" in ls:
            old_home = ls.split("=", 1)[1].strip().strip('"')

    bad = False
    if old_exe:
        try:
            if not Path(old_exe).is_file():
                bad = True
        except OSError:
            bad = True
    if old_home and not bad:
        try:
            oh = Path(old_home)
            if not (oh / "python.exe").is_file():
                bad = True
        except OSError:
            bad = True
    if not bad:
        return False

    pick = _pick_python_for_pyvenv_repair()
    if not pick:
        return False
    home = pick.parent

    new_lines: list[str] = []
    for line in text.splitlines():
        ls = line.strip()
        low = ls.lower()
        if low.startswith("home") and "=" in ls:
            new_lines.append(f"home = {home}")
        elif low.startswith("executable") and "=" in ls:
            new_lines.append(f"executable = {pick}")
        elif low.startswith("command") and "=" in ls:
            new_lines.append(f"command = {pick} -m venv {APP_ROOT / '.venv'}")
        else:
            new_lines.append(line)
    try:
        cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


# cv 组 process_video.py 路径，可通过环境变量 PROCESS_VIDEO_SCRIPT 覆盖
# 默认: 项目根目录下的 cv/process_video.py
PROCESS_VIDEO_SCRIPT = Path(
    os.environ.get("PROCESS_VIDEO_SCRIPT", str(APP_ROOT / "cv" / "process_video.py"))
).resolve()

# Keep filenames safe (avoid path traversal / weird chars)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(original: str) -> str:
    name = Path(original).name  # strips any paths sent by client
    name = name.strip().strip(".")
    if not name:
        name = "upload"
    name = _SAFE_NAME_RE.sub("_", name)
    # avoid ridiculously long names on Windows
    return name[:180]


async def _save_upload(upload: UploadFile, dest: Path) -> int:
    """Save UploadFile to dest, returning bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    # Use binary mode and write in chunks to support big videos
    with dest.open("wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)  # 1MB
            if not chunk:
                break
            f.write(chunk)
            written += len(chunk)
    await upload.close()
    return written


# 上传触发 CV 的日志，便于排查“未触发”
_TRIGGER_LOG = APP_ROOT / "cv" / "upload_trigger.log"
# 与 cv/process_video.py 写入的同一文件，上传后先写「处理中」避免残留旧机器上的 success:true
RUN_STATUS_PATH = APP_ROOT / "cv" / "run_status.json"
_CV_LAUNCH_LOG = APP_ROOT / "cv" / "cv_launch.log"


def _clear_cv_result_json_for_new_video() -> None:
    """
    新视频上传时删除上一段视频的 result.json。
    否则 frames 已被新抽帧覆盖，但 OCR 文案仍指向旧内容（甚至旧电脑绝对路径），
    前端用 frame_5.jpg 显示新图、用 JSON 显示旧字，就会「图文不一致」。
    """
    try:
        if CV_RESULT_PATH.is_file():
            CV_RESULT_PATH.unlink()
    except OSError:
        pass


def _prune_cv_result_json_if_foreign_paths() -> None:
    """
    启动时：若 result.json 里 frame_path 不在当前项目目录下（例如从别的电脑拷来的包），
    整文件删除，避免检索/接口一直返回异机上的路径与 OCR。
    """
    if not CV_RESULT_PATH.is_file():
        return
    try:
        raw = json.loads(CV_RESULT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, list) or not raw:
        return
    root_lower = str(APP_ROOT.resolve()).lower().rstrip("\\/") + os.sep

    def _path_ok(s: str) -> bool:
        s = (s or "").strip()
        if not s:
            return True
        p = Path(s)
        # 新版 JSON 为相对路径，不依赖当前工作目录解析
        if not p.is_absolute():
            return True
        try:
            return str(p.expanduser().resolve()).lower().startswith(root_lower)
        except OSError:
            return False

    for it in raw:
        if not isinstance(it, dict):
            continue
        fp = str(it.get("frame_path") or "")
        if fp and not _path_ok(fp):
            try:
                CV_RESULT_PATH.unlink()
            except OSError:
                pass
            try:
                _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
                    f.write(
                        f"[{datetime.now().isoformat()}] 已删除含异机/异路径 frame_path 的 "
                        f"cv/result.json（避免与当前 frames 图文不一致）\n"
                    )
            except OSError:
                pass
            return


def _write_pending_cv_status(video_path: Path) -> None:
    """
    上传成功后立即覆盖 run_status。
    若仍保留旧环境里的 success:true，前端会一直用旧 video_name 比对，新视频永远不「就绪」。
    """
    try:
        _clear_cv_result_json_for_new_video()
        RUN_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat()
        payload = {
            "ran": True,
            "success": False,
            "video_path": str(video_path.resolve()),
            "video_name": video_path.name,
            "message": "视频已上传，CV 处理中…",
            "frame_count": None,
            "started_at": now,
            "finished_at": None,
            "error": None,
        }
        RUN_STATUS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        try:
            errp = APP_ROOT / "cv" / "_write_pending_cv_status_error.log"
            errp.write_text(
                f"{datetime.now().isoformat()}\n{repr(e)}\n",
                encoding="utf-8",
            )
        except Exception:
            pass


def _write_cv_subprocess_failed_status(video_path: Path, returncode: int) -> None:
    """子进程非 0 退出时更新 run_status，避免一直显示「处理中」却无 frames/result。"""
    try:
        now = datetime.now().isoformat()
        hint = (
            "常见原因：.venv 内 numpy/OpenCV 与当前 Python 版本不匹配（换机或升级 D:\\\\python 后）。"
            "请在项目根目录执行: .venv\\\\Scripts\\\\python.exe -m pip install --force-reinstall numpy opencv-python"
            "（或运行 fix_cv_deps.bat），并查看 cv\\\\cv_launch.log"
        )
        payload = {
            "ran": True,
            "success": False,
            "video_path": str(video_path.resolve()),
            "video_name": video_path.name,
            "message": f"CV 处理失败（退出码 {returncode}）。{hint}",
            "frame_count": 0,
            "started_at": now,
            "finished_at": now,
            "error": f"subprocess returncode={returncode}",
        }
        RUN_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_STATUS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _check_venv_cv_imports() -> tuple[bool, str]:
    """
    检测「将用于 CV 子进程」的 Python 能否 import numpy、cv2。
    不匹配时 CV 会在 import 阶段秒退，frames/result 不会更新。
    """
    py = Path(_python_for_cv_subprocess())
    if not py.is_file():
        return False, f"解释器不存在: {py}"
    try:
        r = subprocess.run(
            [
                str(py),
                "-c",
                "import numpy, cv2; print('ok', numpy.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(APP_ROOT),
            env=_cv_subprocess_env(),
        )
        if r.returncode == 0:
            return True, (r.stdout or "").strip() or "ok"
        err = (r.stderr or r.stdout or "").strip()
        return False, err[:2500] if err else f"exit {r.returncode}"
    except subprocess.TimeoutExpired:
        return False, "检测超时（import numpy/cv2）"
    except Exception as e:
        return False, str(e)


_CV_IMPORT_CHECK_CACHE: tuple[float, bool, str] | None = None


def _check_venv_cv_imports_cached(ttl_sec: float = 60.0) -> tuple[bool, str]:
    """与 _check_venv_cv_imports 相同，但短时缓存，避免 /health 频繁拉起子进程。"""
    global _CV_IMPORT_CHECK_CACHE
    now = time.time()
    if _CV_IMPORT_CHECK_CACHE is not None:
        ts, ok, detail = _CV_IMPORT_CHECK_CACHE
        if (now - ts) < ttl_sec:
            return ok, detail
    ok, detail = _check_venv_cv_imports()
    _CV_IMPORT_CHECK_CACHE = (now, ok, detail)
    return ok, detail


def _trigger_process_video(video_path: Path) -> None:
    """在后台运行 cv 组的 process_video.py 处理视频。不阻塞请求。"""
    video_abs = video_path.resolve()

    def _log_msg(msg: str) -> None:
        try:
            _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass

    def _wait_done(p: subprocess.Popen, vpath: Path, log_f) -> None:
        """在后台线程里等待子进程结束，把退出码写入日志（便于排查秒退/依赖缺失）。"""
        try:
            rc = p.wait()
            _log_msg(f"CV 子进程结束 pid={p.pid} returncode={rc} video={vpath}")
            if rc != 0:
                # process_video 在 import 失败时会先写入含 error 的 run_status，避免覆盖详细原因
                try:
                    if RUN_STATUS_PATH.exists():
                        prev = json.loads(RUN_STATUS_PATH.read_text(encoding="utf-8"))
                        if prev.get("finished_at") and (
                            prev.get("error")
                            or "依赖加载失败" in (prev.get("message") or "")
                        ):
                            return
                except Exception:
                    pass
                _write_cv_subprocess_failed_status(vpath, rc)
        except Exception as e:
            _log_msg(f"等待子进程异常: {e}")
        finally:
            try:
                log_f.close()
            except Exception:
                pass

    if not PROCESS_VIDEO_SCRIPT.exists():
        _log_msg(f"跳过: 脚本不存在 {PROCESS_VIDEO_SCRIPT}")
        return
    if not video_abs.exists():
        _log_msg(f"跳过: 视频不存在 {video_abs}")
        return
    python_exe = _python_for_cv_subprocess()
    launch_f = None
    try:
        _log_msg(f"启动 CV: python={python_exe} script={PROCESS_VIDEO_SCRIPT} video={video_abs}")
        # 子进程在 import cv2 等阶段若失败，不能再用 DEVNULL，否则「像没触发」
        _CV_LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        launch_f = open(_CV_LAUNCH_LOG, "a", encoding="utf-8")
        launch_f.write(f"\n==== [{datetime.now().isoformat()}] ====\n")
        launch_f.write(f"cmd: {python_exe} {PROCESS_VIDEO_SCRIPT} {video_abs}\n")
        launch_f.flush()
        kwargs: dict = {
            "stdout": launch_f,
            "stderr": subprocess.STDOUT,
            "cwd": str(APP_ROOT),
            "env": _cv_subprocess_env(),
        }
        if not _IS_WINDOWS:
            kwargs["start_new_session"] = True
        else:
            # 不弹黑窗（Windows）；无该常量时跳过
            _cnw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if _cnw:
                kwargs["creationflags"] = _cnw
        p = subprocess.Popen(
            [python_exe, str(PROCESS_VIDEO_SCRIPT), str(video_abs)],
            **kwargs,
        )
        _log_msg(f"已启动子进程 pid={p.pid}")
        threading.Thread(
            target=_wait_done, args=(p, video_abs, launch_f), daemon=True
        ).start()
    except Exception as e:
        _log_msg(f"启动失败: {e}")
        if launch_f is not None:
            try:
                launch_f.close()
            except Exception:
                pass


app = FastAPI(title="FastAPI 文件上传服务")

# 浏览器从 Vite(5173) 访问本机 8000 属于跨域，必须开启 CORS，否则前端 fetch 会失败并显示「后端未连接」
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    # Vite 在 5173 被占用时会改用 5174、5175…，否则浏览器直连 8000 会 CORS 拦截
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):(51[7-9]\d|3000)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 供前端「精选截图」拉取抽帧图片（与 cv/result 返回的 frame_url 对应）
_CV_FRAMES_DIR = APP_ROOT / "cv" / "frames"
_CV_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/cv/frames", StaticFiles(directory=str(_CV_FRAMES_DIR)), name="cv_frames")

# 挂载上传目录，支持历史记录回放时通过 /videos/{saved_path} 访问视频
_UPLOADS_DIR = APP_ROOT / "uploads"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/videos", StaticFiles(directory=str(_UPLOADS_DIR)), name="videos")

# 挂载前端静态文件（npm run build 产物），挂载到 /app 路径避免拦截 API 路由
_FRONTEND_DIR = APP_ROOT.parent.parent / "upload-demo" / "dist"
if _FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


@app.on_event("startup")
def startup():
    # 先修正 JSON 内异机绝对路径 → frames/frame_X.jpg（不依赖 CV 是否跑成功）
    try:
        if normalize_result_json_frame_paths():
            _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
                f.write(
                    f"[{datetime.now().isoformat()}] 已自动将 cv/result.json 中的 frame_path "
                    f"规范为相对路径 frames/frame_*.jpg\n"
                )
    except Exception:
        pass
    try:
        _prune_cv_result_json_if_foreign_paths()
    except Exception:
        pass
    try:
        if _repair_venv_pyvenv_cfg():
            _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
                f.write(
                    f"[{datetime.now().isoformat()}] 已自动修复 .venv/pyvenv.cfg 中无效的旧机器 Python 路径（换电脑后 CV 子进程才能跑）\n"
                )
    except Exception:
        pass
    database.init_db()
    try:
        _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
        cv_py = _python_for_cv_subprocess()
        ok, detail = _check_venv_cv_imports()
        health_path = APP_ROOT / "cv" / "venv_cv_health.log"
        with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"[{datetime.now().isoformat()}] 服务启动: FastAPI sys.executable={sys.executable}\n"
            )
            f.write(
                f"[{datetime.now().isoformat()}] 服务启动: CV 子进程将实际使用 python={cv_py}\n"
            )
            f.write(
                f"[{datetime.now().isoformat()}] CV 依赖自检 import numpy+cv2: "
                f"{'OK ' + detail if ok else 'FAIL — ' + detail[:500]}\n"
            )
        try:
            health_path.write_text(
                f"{datetime.now().isoformat()}\n"
                f"python={cv_py}\n"
                f"numpy_cv2_ok={ok}\n"
                f"{detail}\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        if not ok:
            with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
                f.write(
                    f"[{datetime.now().isoformat()}] 若上传后 frames/result 不更新："
                    f"在项目根目录运行 fix_cv_deps.bat，或执行 "
                    f".venv\\\\Scripts\\\\python.exe -m pip install --force-reinstall numpy opencv-python\n"
                )
    except Exception:
        pass


@app.get("/health")
def health():
    cv_ok, cv_detail = _check_venv_cv_imports_cached()
    _lp = lecture_key_probe()
    return {
        "ok": True,
        "deepseek_configured": bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip()),
        "lecture_probe": _lp,
        "lecture_configured": bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip()),
        "dotenv_path": str(_ENV_FILE),
        "dotenv_file_exists": _ENV_FILE.is_file(),
        "cv_result_path": str(CV_RESULT_PATH),
        "cv_result_exists": CV_RESULT_PATH.exists(),
        "cv_venv_python": _python_for_cv_subprocess(),
        "cv_numpy_cv2_import_ok": cv_ok,
        "cv_numpy_cv2_detail": cv_detail[:500] if not cv_ok else cv_detail,
    }


@app.get("/debug/retrieval")
def debug_retrieval():
    """诊断：OCR 结果文件是否存在、检索到的上下文预览"""
    exists = CV_RESULT_PATH.exists()
    context_preview = ""
    err = None
    if exists:
        try:
            ctx = retrieve_context("测试")
            context_preview = (ctx[:500] + "...") if len(ctx) > 500 else ctx
        except Exception as e:
            err = str(e)
    return JSONResponse({
        "result_json_path": str(CV_RESULT_PATH),
        "result_json_exists": exists,
        "context_length": len(retrieve_context("")) if exists else 0,
        "context_preview": context_preview or "(空)",
        "error": err,
    })


@app.api_route("/cv/sync-frame-paths", methods=["GET", "POST"])
def cv_sync_frame_paths():
    """
    手动触发：把 cv/result.json 里组员机器上的绝对路径全部改成 frames/frame_X.jpg。
    （启动时与每次读结果/检索时也会自动执行，此接口便于你确认已修正。）
    """
    fixed = normalize_result_json_frame_paths()
    return {
        "ok": True,
        "rewrote_disk": fixed,
        "message": "已把 frame_path 规范为相对路径 frames/frame_{秒}.jpg（若文件不存在则无操作）",
    }


@app.get("/cv/status")
def cv_status():
    """查询 Test02/process_video 是否运行过及最近一次处理状态"""
    if not RUN_STATUS_PATH.exists():
        return JSONResponse({
            "ran": False,
            "message": "从未运行过视频处理",
            "success": None,
            "video_path": None,
            "video_name": None,
            "frame_count": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
        })
    try:
        data = json.loads(RUN_STATUS_PATH.read_text(encoding="utf-8"))
        return JSONResponse({"ran": True, **data})
    except Exception as e:
        return JSONResponse({"ran": False, "message": "状态文件读取失败", "error": str(e)})


@app.get("/cv/result")
def cv_result(
    limit: int = Query(3, ge=1, le=50),
    video_id: str = Query(""),
):
    """
    返回最近一次 CV 写入的 result.json 片段，供前端展示截图与文案。
    video_id 预留与多视频对齐；当前实现与单文件 result.json 一致。
    """
    _ = video_id  # 预留：多视频时可按 id 过滤
    path = APP_ROOT / "cv" / "result.json"
    if not path.exists():
        return {"ok": True, "items": []}
    # 覆盖 zip 里组员机器上的绝对路径 → 本项目 frames/ 相对路径
    normalize_result_json_frame_paths()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "items": []}
    if not isinstance(raw, list):
        return {"ok": False, "items": []}

    items = []
    for it in raw[:limit]:
        if not isinstance(it, dict):
            continue
        fp = str(it.get("frame_path") or "")
        if not _frame_path_belongs_to_this_project(fp):
            continue
        name = Path(fp).name
        frame_url = f"/cv/frames/{name}" if name else None
        items.append(
            {
                "time_offset": it.get("time_offset"),
                "text": it.get("text", ""),
                "frame_url": frame_url,
            }
        )
    return {"ok": True, "items": items}


@app.get("/cv/search")
def cv_search(q: str = Query("", min_length=1, max_length=200)):
    """
    按关键词在 OCR 结果中检索视频片段（与 nlp_retrieval.search_video_segments 一致）。
    返回每条含 title、snippet、time_text、seconds，供前端跳转播放。
    """
    normalize_result_json_frame_paths()
    items = search_video_segments(q.strip(), limit=20)
    return {"ok": True, "query": q.strip(), "items": items}


@app.get("/cv/lecture")
def cv_lecture(
    refresh: int = Query(0, ge=0, le=1),
):
    """
    读取 cv/result.json 中的 OCR 文本，调用 DeepSeek 生成讲义总结（供前端「文字讲义 / 总结」）。
    使用与 /chat 相同的 DEEPSEEK_API_KEY。refresh=1 时忽略本地缓存并重新请求。
    """
    normalize_result_json_frame_paths()
    return JSONResponse(get_lecture_summary(refresh=bool(refresh)))


@app.post("/upload")
async def upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if file is None:
        raise HTTPException(status_code=400, detail="必须提供 file 字段")

    original_name = file.filename or "upload"
    safe_name = _safe_filename(original_name)

    # Preserve extension if present
    ext = Path(safe_name).suffix
    if not ext and file.content_type:
        # best-effort extension mapping for common videos
        if file.content_type == "video/mp4":
            ext = ".mp4"
        elif file.content_type == "video/webm":
            ext = ".webm"

    token = secrets.token_hex(8)
    final_name = f"{Path(safe_name).stem}_{token}{ext}" if ext else f"{safe_name}_{token}"
    dest = UPLOAD_DIR / final_name

    try:
        size = await _save_upload(file, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")

    # 写入数据库：视频元数据（标题、上传时间、向量索引路径占位）
    saved_rel = str(dest.relative_to(APP_ROOT)).replace("\\", "/")
    upload_time = datetime.now().isoformat()
    # 向量索引路径：由 NLP 组处理后更新，此处先留空或占位
    vector_index_path = ""
    video_id = database.insert_video_metadata(
        title=original_name,
        upload_time=upload_time,
        vector_index_path=vector_index_path,
        saved_path=saved_rel,
    )

    # 视频保存成功后，后台触发 cv 组 process_video.py 处理
    try:
        _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_TRIGGER_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] 上传成功，准备触发 CV: {dest}\n")
    except Exception:
        pass
    # 立刻覆盖 run_status，避免磁盘上仍是「旧视频 success:true」导致前端轮询永远对不上新文件名
    _write_pending_cv_status(dest)
    # 用 BackgroundTasks：在响应返回后由 Starlette 在线程池里执行，
    # 比 asyncio.create_task+to_thread 在 Windows/Uvicorn 下更稳定，避免“上传成功但 CV 没跑”
    background_tasks.add_task(_trigger_process_video, dest)

    return JSONResponse(
        {
            "id": video_id,
            "filename": original_name,
            "saved_as": saved_rel,
            "content_type": file.content_type,
            "size_bytes": size,
        }
    )


@app.get("/videos")
def list_videos():
    """获取所有视频元数据（数据库）"""
    return JSONResponse(database.get_all_videos())


class ChatRequest(BaseModel):
    question: str
    # 前端上传后用 saved_as（路径字符串）当 video_id，不能写成 int 否则 POST /chat 会 422
    video_id: int | str | None = None
    target_seconds: float | None = None  # 前端解析出的目标秒，优先用于「某秒讲了什么」检索


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    问答接口：接收用户问题 -> NLP 检索（支持按秒）-> DeepSeek 或本地检索回答 -> SSE 流式返回
    请求体: {"question": "...", "target_seconds": 30.0 可选}
    响应: Server-Sent Events 流，每行 data: {"type":"content","text":"..."}
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="必须提供 question 字段")

    async def event_stream():
        async for chunk in stream_chat(
            question,
            target_seconds=req.target_seconds,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

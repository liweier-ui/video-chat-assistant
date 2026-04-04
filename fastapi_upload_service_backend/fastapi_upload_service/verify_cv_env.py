"""
CV 依赖自检：请用「将要运行 main.py 的同一个 python」执行：
  python verify_cv_env.py
"""
from __future__ import annotations

import sys


def main() -> int:
    print("Python:", sys.executable)
    ok = True

    def check(name: str, import_fn) -> None:
        nonlocal ok
        try:
            import_fn()
            print(f"  [OK] {name}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            ok = False

    print("检查核心模块…")
    check("cv2", lambda: __import__("cv2"))
    check("paddleocr", lambda: __import__("paddleocr"))

    print("检查 FER（表情，可选）…")
    try:
        # 与 cv/process_video.py 一致：部分版本 fer 包 __init__ 不导出 FER
        try:
            from fer.fer import FER  # type: ignore
        except ImportError:
            from fer import FER  # type: ignore
        _ = FER
        print("  [OK] FER 类可用")
    except Exception as e:
        print(f"  [WARN] FER: {e}（脚本会跳过表情，仍可 OCR）")

    print()
    if ok:
        print("核心依赖正常。可直接 python main.py 启动后端并上传视频。")
        return 0
    print("请先在本机执行: pip install -r requirements.txt")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

# 后端本地运行（你只需要上传视频时）

## 一次性准备

1. 双击 **`install_deps.bat`**（或手动：`pip install -r requirements.txt`）  
   使用与启动后端 **同一个** Python。

2. （可选）命令行执行：`python verify_cv_env.py`  
   看到 `[OK] cv2`、`paddleocr` 即可。

## 每次开发

1. 双击 **`start_backend.bat`**，保持窗口不关。  
2. 再启动前端（`upload-demo` 的 `npm run dev`）。  
3. 浏览器上传视频即可。

## 说明

- 若用 **`.venv` 里的 python** 启动 FastAPI，CV 会尽量改用 **PATH 里第一个不在 `.venv` 下的 `python.exe`**（或 `D:\python\python.exe` 等常见路径），否则子进程秒退，**不会出现 `cv/frames` 与 `result.json` 更新**。  
- **`start_backend.bat`** 若检测到 `D:\python\python.exe` 会自动设置 **`CV_PYTHON`**。其它机器可在系统环境变量或 `.env` 里设 `CV_PYTHON=你的python.exe`。  
- 日志：`cv/upload_trigger.log`（看「启动 CV: python=」必须是**非 .venv**）、`cv/cv_launch.log`、`cv/process_video_stderr.log`。

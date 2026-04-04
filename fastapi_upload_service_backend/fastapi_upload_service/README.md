# FastAPI 上传服务（视频抽帧 + OCR + 检索/对话）

本项目提供：**上传视频 → 后台抽帧 → 面部表情（可选）+ 文字 OCR → 生成 `cv/result.json`**，供检索与 LLM 对话使用。

拿到代码的人按下面步骤即可在本机跑通。

---

## 一、环境要求

| 项目 | 说明 |
|------|------|
| **操作系统** | **Windows 10/11 x64**（当前脚本与 Paddle 路径说明以 Windows 为主） |
| **Python** | **3.10～3.13**（须与 `paddlepaddle` 官方轮子匹配；安装时勾选 **Add Python to PATH**） |
| **磁盘与网络** | 预留数 GB；**首次 OCR** 会从镜像站下载模型（仅一次，之后走本地缓存） |
| **运行库** | 建议安装 **Microsoft Visual C++ 2015–2022 可再发行组件（x64）**（Paddle 等依赖常见需要） |

**路径建议**：项目放在 **纯英文路径** 下（例如 `D:\work\fastapi_upload_service`），避免部分工具链对中文路径支持不佳。代码已把 Paddle 模型缓存固定到 `cv/.paddlex_cache`，减轻「用户文件夹含中文」导致的问题。

---

## 二、快速开始（推荐：项目内虚拟环境）

在 **项目根目录**（含 `main.py`、`requirements.txt` 的目录）打开 **命令提示符 CMD** 或 PowerShell：

### 1. 创建并启用虚拟环境

```bat
cd /d "你的项目路径"
py -3 -m venv .venv
.venv\Scripts\activate
```

### 2. 安装依赖

```bat
python -m pip install -U pip
pip install -r requirements.txt
```

也可双击运行 **`install_deps.bat`**（会使用当前 `python`，请确认与上面一致）。

### 3. 自检 CV/OCR 依赖

必须用 **将要运行后端的同一个 Python**（建议始终用 `.venv`）：

```bat
.venv\Scripts\python.exe verify_cv_env.py
```

看到 `cv2`、`paddleocr` 为 `[OK]` 即可。

### 4. 配置环境变量（可选）

- 复制 **`.env.example`** 为 **`.env`**，按说明填写 **大模型 API**、数据库等（具体键名以 `.env.example` 为准）。
- 端口默认 **8000**，可用环境变量 **`PORT`** 修改。

**文字讲义 / 总结**：接口 **`GET /cv/lecture`** 读取 `cv/result.json` 中的 OCR 文本，调用 **DeepSeek** 生成讲义，与 **`/chat` 共用 `DEEPSEEK_API_KEY`**。`?refresh=1` 可强制忽略缓存重新生成。可选 **`DEEPSEEK_LECTURE_MODEL`**（默认 `deepseek-chat`）。

### 5. 启动后端

**方式 A（推荐）**：双击 **`start_backend.bat`**

**方式 B**：

```bat
.venv\Scripts\activate
python main.py
```

浏览器访问：**http://127.0.0.1:8000**（若改了 `PORT` 则换端口）。

> **重要**：视频处理子进程会优先使用 **项目下的 `.venv\Scripts\python.exe`**，与 `numpy` / `opencv` / `paddle` 的 ABI 一致。不要随意把 `CV_PYTHON` 指到另一套 Python，除非你知道自己在做什么（高级排障可用 `CV_FORCE_CV_PYTHON=1`）。

---

## 三、视频与 CV 结果说明

1. 在前端或通过上传接口提交视频后，后台会调用 **`cv/process_video.py`**。
2. 产出目录（均在项目内）：
   - **`cv/frames/`**：抽帧图片 `frame_0.jpg`、`frame_5.jpg`…
   - **`cv/result.json`**：每帧合并后的「表情 + 文字」等结果（供检索使用）
   - **`cv/run_status.json`**：是否处理完成、`finished_at` 等状态
   - **`cv/process_video_stderr.log`**：**排障必看**（OCR 进度、报错栈）
3. **第一次**跑 OCR 时，模型会下载到 **`cv/.paddlex_cache/`**，耗时与网速有关；完成后会快很多。

### OCR 速度与「等很久」

- 在 **CPU** 上，默认识别为 **轻量模型 + 关闭文字行方向 + 长边缩放**，比「server 模型 + 方向」快很多。
- 若需更准、更慢，可在运行环境中设置（再启动后端）：
  - `CV_OCR_SERVER=1` → 使用 server 级检测/识别模型  
  - `CV_OCR_TEXTLINE_ORI=1` → 打开文字行方向（每帧多一次推理）  
- `CV_OCR_MAX_SIDE`：默认 `1280`，识别前缩小大图；设为 `0` 表示不缩放。

### 同步帧路径（换电脑/拷贝项目后）

若 `result.json` 里残留了别的机器上的绝对路径，可调用接口 **`GET/POST /cv/sync-frame-paths`**（见 `main.py` 说明），会规范为 `frames/frame_*.jpg`。

### 视频内容搜索（前端「搜索片段」）

- 接口：**`GET /cv/search?q=关键词`**（`q` 最长 200 字符）。
- 逻辑：在 **`cv/result.json`** 各帧的 OCR 文本中做**关键词子串匹配**（支持空格、`/`、逗号等分隔多个词），按命中数排序，最多返回 20 条。
- 需先完成视频处理并生成 OCR，否则结果为空。

---

## 四、常见问题与修复脚本

| 现象 | 处理 |
|------|------|
| `import cv2` / `numpy` 报错 | 在项目根执行 **`fix_cv_deps.bat`**，或：`.venv\Scripts\python.exe -m pip install --force-reinstall numpy opencv-python` |
| `paddle` / `libpaddle` / `shm.dll` 等 DLL 错误 | 安装 **VC++ 2015–2022 x64**；再执行 **`fix_ocr_deps.bat`**（会重装 torch CPU、paddlepaddle 等） |
| 上传后 `result.json` 长期「正在识别…」 | 打开 **`cv/process_video_stderr.log`**，看是否有 `[OCR] 第 i/n 帧` 在增长；CPU 慢时属正常，请耐心等待 |
| 仅表情失败、OCR 正常 | 可忽略 FER 警告；或检查 `fer` 依赖 |

---

## 五、目录与脚本一览（给接手的人）

| 文件/目录 | 作用 |
|-----------|------|
| `main.py` | FastAPI 入口，`python main.py` 启动 |
| `requirements.txt` | Python 依赖版本 |
| `install_deps.bat` | 一键 `pip install -r requirements.txt` |
| `start_backend.bat` | 启动后端（UTF-8 + 当前目录） |
| `verify_cv_env.py` | 检查 cv2、paddleocr 等是否可用 |
| `fix_cv_deps.bat` | 修复 numpy/opencv 与 venv 不一致 |
| `fix_ocr_deps.bat` | 修复 Paddle/torch 等 OCR 相关依赖 |
| `cv/process_video.py` | 抽帧 + 表情 + OCR 主流程 |
| `cv/.paddlex_cache/` | Paddle 模型缓存（可删后重新下载，**已加入 .gitignore**） |

---

## 六、开发说明

- 修改后端代码后，若用 `main.py` 启动且带 **reload**，一般会自动重载；**视频处理是独立子进程**，改 `cv/process_video.py` 后 **新上传的任务**才会用新逻辑。
- 不要将含密钥的 `.env` 提交到仓库；`cv/result.json`、`cv/run_status.json` 等运行时文件通常也不提交。

---

如在其它机器上按上述步骤仍无法运行，请提供：**Python 版本**、**`verify_cv_env.py` 完整输出**、以及 **`cv/process_video_stderr.log` 末尾 50 行**，便于排查。

# 后端升级说明

## 新增功能

### 任务 A：LLM API 集成与流式问答

- **DeepSeek API 对接**：集成国产大模型 DeepSeek，通过环境变量 `DEEPSEEK_API_KEY` 配置
- **`POST /chat` 接口**：接收用户问题 → 调用 NLP 检索函数 → 调用大模型 API → **流式返回** AI 回答

**使用方式：**

```bash
# 1. 配置 API Key（二选一）
# 方式一：创建 .env 文件，写入：
DEEPSEEK_API_KEY=sk-your-api-key

# 方式二：设置环境变量
set DEEPSEEK_API_KEY=sk-your-api-key   # Windows
export DEEPSEEK_API_KEY=sk-your-api-key  # Linux/Mac

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python main.py

# 4. 调用流式问答
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"question\":\"视频里讲了什么？\"}"
```

**流式响应格式：** Server-Sent Events，每行 `data: {"type":"content","text":"..."}` 或 `data: {"type":"error","text":"错误信息"}`

### 任务 B：数据库升级

- **SQLite 存储**：视频元数据写入 `data/videos.db`
- **字段**：`id`, `title`, `upload_time`, `vector_index_path`, `saved_path`
- **`GET /videos`**：查询所有视频元数据
- **上传接口增强**：`POST /upload` 返回中新增 `id` 字段

### NLP 检索接口

- **`nlp_retrieval.retrieve_context(question)`**：供 NLP 组对接
- 当前为占位实现，从 `cv/result.json` 读取 OCR 结果作为上下文
- NLP 组可替换为真实向量检索（如 Milvus、FAISS 等）

## 目录结构

```
fastapi_upload_service/
├── main.py           # 主应用：/upload, /chat, /videos
├── database.py       # SQLite 视频元数据
├── nlp_retrieval.py  # NLP 检索接口（可替换）
├── llm_chat.py       # DeepSeek 流式对话
├── data/videos.db    # SQLite 数据库（自动创建）
├── .env.example      # 环境变量示例
└── requirements.txt
```

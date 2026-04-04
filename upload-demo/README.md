# 智能视频问答前端（upload-demo）

该前端默认对接 FastAPI 后端：
`D:\qianduan\fastapi_upload_service (5)\fastapi_upload_service`

## 1) 启动后端

在后端目录执行：

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## 2) 启动前端

在当前目录执行：

```bash
npm install
npm run dev
```

浏览器打开：`http://127.0.0.1:5173`

## 3) 前端后端连接说明

- 默认后端地址：`http://127.0.0.1:8000`
- 健康检查：`GET /health`
- 上传接口：`POST /upload`
- 对话接口：`POST /chat`（SSE 流式）
- OCR 状态：`GET /cv/status`
- OCR 结果：`GET /cv/result`

## 4) 修改后端地址（可选）

在前端目录新建 `.env.development.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

保存后重启前端即可生效。

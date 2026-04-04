# FastAPI 后端服务器

> ⚠️ **已弃用（Deprecated）**：本目录 `upload-demo/backend` 是早期演示用后端，接口是 `/api/upload`。
>
> 你当前这个项目前端实际对接的是后端组提供的：
> `D:\qianduan\fastapi_upload_service (2)\fastapi_upload_service`
>
> - 上传：`POST /upload`
> - 视频：`GET /media/{...}`（支持 Range，保证可拖动/可跳转）
> - 对话：`POST /chat`
>
> 请使用 `upload-demo/启动后端.bat` 来启动后端组版本，避免跑错。

## 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

## 启动服务器

```bash
python main.py
```

或者使用 uvicorn 直接启动：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## API 接口

- **健康检查**: `GET http://localhost:8000/api/health`
- **文件上传**: `POST http://localhost:8000/api/upload`
- **API 文档**: `http://localhost:8000/docs` (Swagger UI)
- **ReDoc 文档**: `http://localhost:8000/redoc`

## 返回数据格式

```json
{
  "success": true,
  "message": "文件上传成功",
  "filename": "example.txt",
  "fileSize": 12345,
  "data": [
    {
      "id": 1,
      "text": "文本内容...",
      "timestamp": "2024-02-04 15:30:00"
    }
  ]
}
```

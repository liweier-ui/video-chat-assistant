from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List
from datetime import datetime
import uvicorn

app = FastAPI(title="文件上传 API", version="1.0.0")

# 配置 CORS，允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # 前端地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 文本列表数据类型
class TextItem:
    def __init__(self, id: int, text: str, timestamp: str):
        self.id = id
        self.text = text
        self.timestamp = timestamp
    
    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "timestamp": self.timestamp
        }

# 生成模拟的文本列表数据
def generate_text_list() -> List[dict]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        TextItem(1, "这是第一条文本内容，从 FastAPI 后端返回的 JSON 数据。", now).to_dict(),
        TextItem(2, "第二条文本：文件上传成功，FastAPI 后端已处理并返回文本列表数据。", now).to_dict(),
        TextItem(3, "第三条文本内容，展示 FastAPI 后端返回的 JSON 数据结构。", now).to_dict(),
        TextItem(4, "第四条：这是一个较长的文本内容，用于测试文本区域的换行和显示效果，确保长文本能够正确展示。这些数据是从 FastAPI 后端服务器返回的真实 JSON 数据。", now).to_dict(),
        TextItem(5, "第五条文本，验证 FastAPI 后端 API 返回数据的完整性和格式。", now).to_dict(),
        TextItem(6, "第六条：这是 FastAPI 后端返回的额外数据，证明 API 正常工作。", now).to_dict(),
    ]

# 健康检查接口
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "message": "FastAPI 后端服务运行正常",
        "version": "1.0.0"
    }

# 文件上传接口
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # 读取文件内容（这里只是演示，实际可以根据需要处理文件）
        contents = await file.read()
        file_size = len(contents)
        
        print(f"收到文件上传: {file.filename}, 大小: {file_size} bytes")
        
        # 模拟处理时间
        import asyncio
        await asyncio.sleep(0.5)  # 模拟 500ms 的处理时间
        
        # 生成并返回文本列表
        text_list = generate_text_list()
        
        return JSONResponse({
            "success": True,
            "message": "文件上传成功",
            "filename": file.filename,
            "fileSize": file_size,
            "data": text_list  # 返回文本列表
        })
        
    except Exception as e:
        print(f"上传处理错误: {e}")
        raise HTTPException(status_code=500, detail=f"服务器处理错误: {str(e)}")

# 启动服务器
if __name__ == "__main__":
    print("\n🚀 FastAPI 后端服务器启动中...")
    print("📍 服务地址: http://localhost:8000")
    print("📤 上传接口: http://localhost:8000/api/upload")
    print("💚 健康检查: http://localhost:8000/api/health")
    print("📚 API 文档: http://localhost:8000/docs\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

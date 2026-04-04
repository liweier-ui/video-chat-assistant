#!/bin/bash
# 自动启动后端服务
cd fastapi_upload_service_backend/fastapi_upload_service
pip install -r requirements.txt -q
echo "后端依赖安装完成，正在启动服务..."
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
echo "后端已启动！访问地址：https://$(curl -s ifconfig.me)-8000.preview.app.github.dev"
echo "查看日志：tail -f server.log"

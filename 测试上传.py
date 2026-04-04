#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试文件上传功能
"""
import requests
import os
from pathlib import Path

# 创建测试文件
test_file_path = Path("test_upload.txt")
test_content = "这是一个测试文件\n" * 100  # 创建一个小文件用于测试
test_file_path.write_text(test_content, encoding='utf-8')

print("=" * 60)
print("🧪 开始测试文件上传...")
print("=" * 60)
print(f"📁 测试文件: {test_file_path.absolute()}")
print(f"📊 文件大小: {test_file_path.stat().st_size} 字节")
print()

try:
    # 发送上传请求
    print("📤 正在上传文件到后端...")
    with open(test_file_path, 'rb') as f:
        files = {'file': ('test_upload.txt', f, 'text/plain')}
        response = requests.post('http://localhost:8000/upload', files=files, timeout=10)
    
    print(f"✅ 上传完成!")
    print(f"📊 响应状态码: {response.status_code}")
    print(f"📋 响应内容:")
    print(response.json())
    print()
    print("=" * 60)
    print("✅ 测试完成！请查看后端命令行窗口的日志输出")
    print("=" * 60)
    
except requests.exceptions.ConnectionError:
    print("❌ 错误: 无法连接到后端服务")
    print("   请确保后端服务正在运行: python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000")
except Exception as e:
    print(f"❌ 错误: {e}")

# 清理测试文件
if test_file_path.exists():
    test_file_path.unlink()
    print(f"🧹 已清理测试文件")

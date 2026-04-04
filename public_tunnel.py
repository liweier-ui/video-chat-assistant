#!/usr/bin/env python3
"""
简单的反向代理 + 公开访问
将本地 8000 端口暴露到 HTTPbin 的 public-tunnel
"""
import http.client
import threading
import time

LOCAL_PORT = 8000
TUNNEL_HOST = "httpbin.org"
TUNNEL_PATH = "/post"

def forward_request(client_conn, client_addr):
    """转发请求到本地后端"""
    try:
        # 读取客户端发来的完整 HTTP 请求
        request = b""
        while True:
            chunk = client_conn.recv(4096)
            request += chunk
            if b"\r\n\r\n" in request or len(chunk) == 0:
                break
            if len(request) > 65536:
                break

        if not request:
            client_conn.close()
            return

        # 发送到 httpbin (测试用)
        headers = {}
        lines = request.decode("utf-8", errors="ignore").split("\r\n")
        if lines:
            method_line = lines[0]
            for line in lines[1:]:
                if ": " in line:
                    k, v = line.split(": ", 1)
                    headers[k] = v

        conn = http.client.HTTPConnection(TUNNEL_HOST, 80, timeout=10)
        conn.request("POST", TUNNEL_PATH, request, headers)
        resp = conn.getresponse()

        response = f"HTTP/1.1 {resp.status} {resp.reason}\r\n"
        for k, v in resp.getheaders():
            response += f"{k}: {v}\r\n"
        response += "\r\n"
        client_conn.sendall(response.encode())
        client_conn.sendall(resp.read())
        conn.close()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        client_conn.close()

def accept_connections(server_sock):
    """接收外部连接"""
    server_sock.listen(5)
    print(f"Proxy listening on 0.0.0.0:{LOCAL_PORT + 1}")
    while True:
        try:
            conn, addr = server_sock.accept()
            t = threading.Thread(target=forward_request, args=(conn, addr), daemon=True)
            t.start()
        except:
            break

if __name__ == "__main__":
    import socket
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LOCAL_PORT + 1))
    print(f"隧道占位脚本。真正的公网暴露需要以下方式之一:")
    print("1. 使用 ngrok: ngrok http 8000")
    print("2. 使用 cloudflared: cloudflared tunnel --url http://localhost:8000")
    print("3. 部署到云服务器(Railway/Render/腾讯云)")
    accept_connections(sock)

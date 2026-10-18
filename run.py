import os
import socket
import uvicorn
from main import app

def start_server():
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    try:
        # Create dual-stack IPv6 socket accepting both IPv4 and IPv6 connections on macOS/Linux
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(('::', port))
        sock.listen(128)
        print(f"Server listening on dual-stack [::]:{port} (IPv4 http://127.0.0.1:{port}/ and IPv6 http://[::1]:{port}/)")
        cfg = uvicorn.Config(app, host=host, port=port, log_level="info")
        srv = uvicorn.Server(cfg)
        srv.run(sockets=[sock])
    except Exception as e:
        print(f"Dual-stack bind fallback: {e}")
        uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()

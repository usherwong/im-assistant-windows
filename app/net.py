# -*- coding: utf-8 -*-
"""网络出口：有代理就用代理，没有就直连。

为什么需要这个：TUN 模式下流量本该被接管，但实测某些域名（例如 Jev 的 api.typesafe.ai）
在 TUN 里 TLS 走不通，而同一个域走 Clash 的混合端口就正常。程序自己不读系统代理，
所以启动时探一遍常见的本地代理端口，能用就把它设成 HTTP(S)_PROXY；探不到就直连（TUN 兜底）。

探测只做一次 CONNECT 握手，不发业务请求，不碰 key。
"""
from __future__ import annotations

import os
import socket

# Clash Verge / Clash for Windows / v2rayN / mihomo 常见混合端口
CANDIDATES = (7897, 7890, 7891, 7899, 10809, 10808, 1080, 8889)
_PROBE_HOST = "api.typesafe.ai"
_PROBE_PORT = 443


def _probe(port: int, host: str = _PROBE_HOST, timeout: float = 1.2) -> bool:
    """这个端口能不能当 HTTP 代理用：连上它、发 CONNECT，看回不回 200。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(
                f"CONNECT {host}:{_PROBE_PORT} HTTP/1.1\r\n"
                f"Host: {host}:{_PROBE_PORT}\r\n\r\n".encode())
            head = b""
            while b"\r\n" not in head and len(head) < 512:
                chunk = sock.recv(256)
                if not chunk:
                    break
                head += chunk
            return b" 200 " in head.split(b"\r\n")[0] + b" "
    except OSError:
        return False


def detect(host: str = _PROBE_HOST) -> int | None:
    """返回第一个可用的本地代理端口，没有就 None。"""
    for port in CANDIDATES:
        if _probe(port, host):
            return port
    return None


def setup(host: str = _PROBE_HOST) -> str:
    """设置进程级代理。返回一句话说明（进日志/状态栏），失败也不抛。

    - 环境里已经有 HTTP(S)_PROXY：尊重它，什么都不做；
    - 探到本地代理：写进 os.environ，之后的 httpx/SDK 调用都会走它；
    - 探不到：直连（TUN 模式下多数站点仍然可用）。
    """
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return "已按环境变量里的代理配置"
    port = detect(host)
    if port is None:
        return "没有探测到本地代理，直连"
    proxy = f"http://127.0.0.1:{port}"
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    return f"走本地代理 {proxy}"


if __name__ == "__main__":
    # 不联网的部分：探测一个肯定没服务的端口 → False
    assert _probe(9, timeout=0.3) is False
    # 已经有环境变量时 setup 不覆盖
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
    assert setup() == "已按环境变量里的代理配置"
    del os.environ["HTTPS_PROXY"]
    msg = setup()
    assert msg.startswith("走本地代理") or msg.startswith("没有探测到")
    print("net ok:", msg)

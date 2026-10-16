"""Run ASGI apps with uvicorn: in the foreground, or in a background thread (tests, evaluation, demo launcher).

On Windows the servers run on a selector event loop instead of uvicorn's default proactor loop. With the proactor loop,
a client that drops its connection at the wrong moment (closing a browser tab with an open event stream is enough)
makes accept() fail with WinError 64, and asyncio then closes the listening socket: the server keeps running but
never accepts another connection. The selector loop does not have that failure.
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import uvicorn


def run_server(server: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
    """server.run(), but on a selector event loop on Windows (see the module docstring)."""
    if sys.platform != "win32":
        server.run(sockets=sockets)
        return
    loop = asyncio.SelectorEventLoop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve(sockets=sockets))
    finally:
        try:
            # What asyncio.run() does on the way out: cancel what is still running (event streams, watchers) and let it finish.
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def run_app(app: Any, host: str, port: int, log_level: str = "warning") -> None:
    """uvicorn.run() for one app in the foreground."""
    run_server(uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level=log_level)))


@dataclass
class BackgroundServer:
    server: uvicorn.Server
    thread: threading.Thread
    host: str
    port: int

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


def serve_in_background(app: Any, host: str = "127.0.0.1", port: int = 0, log_level: str = "warning") -> BackgroundServer:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    actual_port = sock.getsockname()[1]
    config = uvicorn.Config(app, host=host, port=actual_port, log_level=log_level, lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=run_server, args=(server, [sock]), daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.02)
    return BackgroundServer(server=server, thread=thread, host=host, port=actual_port)

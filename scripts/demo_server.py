"""Demo OpenAI-compatible SSE server for trying out faster.

Serves deterministic scripted streaming responses on 127.0.0.1:8098 (override
with --port). Every request gets: role-only chunk -> 4 content tokens at
30ms/20ms intervals -> usage chunk -> [DONE].

Usage:
    python scripts/demo_server.py            # then in another terminal:
    faster bench --base-url http://127.0.0.1:8098/v1 --model demo \
        --num-requests 20 --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json


def sse(obj: object) -> str:
    return f"data: {json.dumps(obj)}\n\n"


SCRIPT: list[tuple[float, str]] = [
    (0.0, sse({"choices": [{"delta": {"role": "assistant"}}]})),
    (0.03, sse({"choices": [{"delta": {"content": "Hello"}}]})),
    (0.02, sse({"choices": [{"delta": {"content": " from"}}]})),
    (0.02, sse({"choices": [{"delta": {"content": " the"}}]})),
    (0.02, sse({"choices": [{"delta": {"content": " demo!"}}]})),
    (0.0, sse({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})),
    (0.0, "data: [DONE]\n\n"),
]


async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        await writer.drain()
        for delay, payload in SCRIPT:
            await asyncio.sleep(delay)
            chunk = payload.encode()
            writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            await writer.drain()
        writer.write(b"0\r\n\r\n")
        await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main(port: int) -> None:
    server = await asyncio.start_server(handler, "127.0.0.1", port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    print(f"demo OpenAI-compatible endpoint: http://{addrs}/v1 (Ctrl+C to stop)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8098)
    asyncio.run(main(parser.parse_args().port))

"""HTTP streaming of one audio file with Range requests (standard library only).

Used by tracker_server.py for the owner's own library files (127.0.0.1 only).
Answers 200 (whole file), 206 (one "bytes=a-b" range, for seeking) or 416
(range not satisfiable).  Knows nothing about the catalogue: the caller
decides which file may be streamed and passes its path and content type.
"""

import re

CHUNK = 256 * 1024


def parse_range(header, size):
    """(start, end) inclusive of one "bytes=a-b" range, None for no header,
    ValueError when it cannot be satisfied."""
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or match.groups() == ("", ""):
        raise ValueError("bad range")
    first, last = match.groups()
    if first == "":  # the last N bytes
        start, end = max(size - int(last), 0), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        raise ValueError("range not satisfiable")
    return start, end


def send_file(handler, path, content_type):
    """Answer the request of a BaseHTTPRequestHandler with the file at `path`
    (honouring its Range header)."""
    size = path.stat().st_size
    try:
        span = parse_range(handler.headers.get("Range"), size)
    except ValueError:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    start, end = span or (0, size - 1)
    handler.send_response(206 if span else 200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(end - start + 1))
    if span:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        with path.open("rb") as stream:
            stream.seek(start)
            left = end - start + 1
            while left > 0:
                data = stream.read(min(CHUNK, left))
                if not data:
                    break
                handler.wfile.write(data)
                left -= len(data)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
        pass  # the player moved on (seek / next track)

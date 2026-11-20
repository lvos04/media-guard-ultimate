"""Lees een eindige lokale SSE-herhaling via ASGI."""

import asyncio


async def sse(app, headers, last):
    """Lees de echte ASGI-route tot na de eindige herlezing, inclusief authenticatie."""
    pad = "/api/v1/events/stream"
    koppen = {**headers, "Last-Event-ID": last}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": pad,
        "raw_path": pad.encode(),
        "query_string": b"",
        "client": ("testclient", 0),
        "server": ("fixture.invalid", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in koppen.items()],
    }
    afgelopen = asyncio.Event()
    eerste = True
    status = None
    delen = []

    async def ontvang():
        nonlocal eerste
        if eerste:
            eerste = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await afgelopen.wait()
        return {"type": "http.disconnect"}

    async def stuur(bericht):
        nonlocal status
        if bericht["type"] == "http.response.start":
            status = bericht["status"]
        elif bericht["type"] == "http.response.body":
            deel = bericht.get("body", b"")
            delen.append(deel)
            if deel.startswith(b"retry:") or not bericht.get("more_body", False):
                afgelopen.set()

    await asyncio.wait_for(app(scope, ontvang, stuur), timeout=8)
    frames = [
        f
        for f in b"".join(delen).decode().split("\n\n")
        if f and not f.startswith("retry:")
    ]
    return status, frames

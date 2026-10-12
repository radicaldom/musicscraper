"""Minimal async client for the slskd REST API (v0)."""

import asyncio
import time
import uuid
from urllib.parse import quote

import httpx


class SlskdError(RuntimeError):
    pass


class Slskd:
    def __init__(self, cfg: dict):
        s = cfg["slskd"]
        self.cfg = s
        self.client = httpx.AsyncClient(
            base_url=s["url"].rstrip("/") + "/api/v0",
            headers={"X-API-Key": s["api_key"]},
            timeout=30,
        )

    async def close(self):
        await self.client.aclose()

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise SlskdError(f"cannot reach slskd at {self.cfg['url']}: {e}") from e
        if r.status_code == 401:
            raise SlskdError("slskd rejected the API key (check [slskd].api_key)")
        if r.status_code >= 400:
            raise SlskdError(f"slskd {method} {path} -> {r.status_code}: {r.text[:200]}")
        return r

    async def check(self) -> dict:
        """Raise unless slskd is reachable and logged in to the Soulseek network."""
        state = (await self._req("GET", "/application")).json()
        server = state.get("server", {})
        if not server.get("isLoggedIn"):
            raise SlskdError(f"slskd is not logged in to Soulseek (state: {server.get('state')})")
        return state

    async def search(self, text: str) -> list[dict]:
        sid = str(uuid.uuid4())
        timeout = int(self.cfg["search_timeout_s"])
        await self._req("POST", "/searches", json={
            "id": sid,
            "searchText": text,
            "searchTimeout": timeout * 1000,
            "responseLimit": 200,
            "fileLimit": 20000,
            "filterResponses": True,
            "maximumPeerQueueLength": int(self.cfg["max_peer_queue"]),
            "minimumResponseFileCount": 1,
        })
        deadline = time.monotonic() + timeout + 15
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                info = (await self._req("GET", f"/searches/{sid}")).json()
                if info.get("isComplete"):
                    break
            return (await self._req("GET", f"/searches/{sid}/responses")).json()
        finally:
            try:
                await self._req("DELETE", f"/searches/{sid}")
            except SlskdError:
                pass

    async def enqueue(self, username: str, files: list[dict]) -> None:
        await self._req(
            "POST",
            f"/transfers/downloads/{quote(username, safe='')}",
            json=[{"filename": f["filename"], "size": f["size"]} for f in files],
        )

    async def downloads(self) -> list[dict]:
        return (await self._req("GET", "/transfers/downloads")).json()

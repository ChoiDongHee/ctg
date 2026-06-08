import asyncio
from pathlib import Path
from typing import Callable, Optional

import aiohttp
import aiofiles

# AlbumDownloader.kt + IP 후보 목록
CANDIDATE_IPS = [
    "192.168.49.64",  # PCAPdroid 확인 — M02C WiFi Direct GO IP
    "192.168.49.1",
    "192.168.43.1",   # Android 핫스팟 기본값
    "192.168.4.1",
    "192.168.1.1",
    "192.168.0.1",
    "10.0.0.1",
]

# 미문서 엔드포인트 탐색 후보
PROBE_PATHS = [
    "/manifest.json",
    "/files/media.config",
    "/stream",
    "/live",
    "/video",
    "/audio/stream",
    "/mjpeg",
    "/camera/stream",
    "/live.mjpeg",
]

MEDIA_EXTENSIONS = {
    "photo": {".jpg", ".jpeg", ".png", ".heic"},
    "video": {".mp4", ".mov", ".m4v"},
    "audio": {".opus", ".wav", ".ogg", ".m4a"},
}


def _classify(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for ftype, exts in MEDIA_EXTENSIONS.items():
        if ext in exts:
            return ftype
    return "misc"


class WiFiTransfer:
    def __init__(self):
        self.glasses_ip: Optional[str] = None

    async def discover_ip(self, hint_ip: str = None, timeout: float = 2.0) -> Optional[str]:
        """후보 IP 병렬 탐색 (BLE에서 받은 hint_ip 우선 시도)"""
        candidates = ([hint_ip] if hint_ip else []) + CANDIDATE_IPS

        async def test(ip: str) -> Optional[str]:
            try:
                async with aiohttp.ClientSession() as s:
                    for path in ["/manifest.json", "/files/media.config"]:
                        try:
                            async with s.get(
                                f"http://{ip}{path}",
                                timeout=aiohttp.ClientTimeout(total=timeout),
                            ) as r:
                                if r.status == 200:
                                    return ip
                        except Exception:
                            continue
            except Exception:
                pass
            return None

        results = await asyncio.gather(*[test(ip) for ip in candidates])
        for ip in results:
            if ip:
                self.glasses_ip = ip
                return ip
        return None

    async def probe_endpoints(self, ip: str) -> dict:
        """미문서 스트리밍 엔드포인트 탐색"""
        results = {}
        async with aiohttp.ClientSession() as s:
            for path in PROBE_PATHS:
                try:
                    async with s.get(
                        f"http://{ip}{path}",
                        timeout=aiohttp.ClientTimeout(total=2.0),
                    ) as r:
                        results[path] = {
                            "status": r.status,
                            "content_type": r.headers.get("Content-Type", ""),
                            "content_length": r.headers.get("Content-Length", "unknown"),
                        }
                except Exception as e:
                    results[path] = {"status": "error", "error": str(e)}
        return results

    async def fetch_manifest(self, ip: str) -> list[dict]:
        """파일 목록 조회: manifest.json → media.config 순서로 시도"""
        async with aiohttp.ClientSession() as s:
            # manifest.json (JSON 형식)
            try:
                async with s.get(f"http://{ip}/manifest.json",
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        return data.get("files", [])
            except Exception:
                pass

            # media.config (plaintext, 한 줄에 파일명 하나 — AlbumDownloader.kt)
            try:
                async with s.get(f"http://{ip}/files/media.config",
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        text = await r.text()
                        files = []
                        for line in text.strip().splitlines():
                            fname = line.strip()
                            if fname:
                                files.append({"filename": fname, "type": _classify(fname)})
                        return files
            except Exception:
                pass

        return []

    async def download_file(
        self,
        session: aiohttp.ClientSession,
        ip: str,
        filename: str,
        save_dir: str,
        progress_cb: Callable = None,
    ) -> str:
        save_path = Path(save_dir) / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if save_path.exists():
            return str(save_path)

        url = f"http://{ip}/files/{filename}"
        downloaded = 0
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            async with aiofiles.open(save_path, "wb") as f:
                async for chunk in r.content.iter_chunked(8192):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        await progress_cb(filename, downloaded, total)

        return str(save_path)

    async def sync_all(
        self,
        save_dir: str = "./downloads",
        hint_ip: str = None,
        progress_cb: Callable = None,
    ) -> list[str]:
        ip = self.glasses_ip or await self.discover_ip(hint_ip)
        if not ip:
            raise RuntimeError("안경 IP를 찾을 수 없습니다.")

        files = await self.fetch_manifest(ip)
        if not files:
            return []

        downloaded = []
        async with aiohttp.ClientSession() as session:
            tasks = [
                self.download_file(
                    session, ip,
                    f["filename"],
                    str(Path(save_dir) / f.get("type", "misc")),
                    progress_cb,
                )
                for f in files
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, str):
                    downloaded.append(r)

        return downloaded

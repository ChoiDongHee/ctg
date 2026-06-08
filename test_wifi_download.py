"""
WiFi 파일 다운로드 테스트
실제 안경이 없으므로 mock HTTP 서버로 테스트
"""
import asyncio
import json
import sys
import io
from pathlib import Path
from aiohttp import web
from core.wifi_transfer import WiFiTransfer

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Mock 안경 파일 목록
MOCK_FILES = [
    "photo_2026_06_05_17_31_45.jpg",
    "photo_2026_06_05_17_32_10.jpg",
    "video_2026_06_05_17_33_20.mp4",
]

# Mock 파일 데이터 (실제로는 JPG/MP4 등)
MOCK_FILE_DATA = {
    "photo_2026_06_05_17_31_45.jpg": b"\xFF\xD8\xFF\xE0" + b"MOCK_JPEG_DATA" * 10000,  # ~150KB
    "photo_2026_06_05_17_32_10.jpg": b"\xFF\xD8\xFF\xE0" + b"MOCK_JPEG_DATA" * 5000,   # ~75KB
    "video_2026_06_05_17_33_20.mp4": b"\x00\x00\x00\x20\x66\x74\x79\x70" + b"MOCK_VIDEO" * 50000,  # ~500KB
}

async def mock_media_config(request):
    """안경 /files/media.config 엔드포인트"""
    return web.Response(
        text="\n".join(MOCK_FILES),
        content_type="text/plain"
    )

async def mock_file_download(request):
    """안경 /files/<filename> 엔드포인트"""
    filename = request.match_info.get('filename')

    if filename not in MOCK_FILE_DATA:
        return web.Response(status=404, text="File not found")

    data = MOCK_FILE_DATA[filename]
    return web.Response(
        body=data,
        content_type="application/octet-stream",
        headers={"Content-Length": str(len(data))}
    )

async def start_mock_server(port=8001):
    """Mock 안경 HTTP 서버 시작 (192.168.49.64 대신 localhost 사용)"""
    app = web.Application()
    app.router.add_get('/files/media.config', mock_media_config)
    app.router.add_get('/files/{filename}', mock_file_download)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()

    print(f"[Mock Server] Started on http://127.0.0.1:{port}")
    return runner

async def test_wifi_transfer():
    """WiFi 파일 다운로드 테스트"""
    # Mock 서버 시작
    runner = await start_mock_server(8001)

    try:
        print("\n[Test] WiFi File Download Pipeline")
        print("=" * 80)

        wifi = WiFiTransfer()
        download_dir = Path("downloads_test/photo")
        download_dir.mkdir(parents=True, exist_ok=True)

        # 1. IP 탐색 (Mock 서버 주소 하드코딩)
        print("\n[Step 1] Discovering glasses IP...")
        mock_ip = "127.0.0.1:8001"
        print(f"  Using mock server: http://{mock_ip}")

        # 2. 파일 목록 조회
        print("\n[Step 2] Fetching manifest...")
        files = await wifi.fetch_manifest("127.0.0.1:8001")
        print(f"  Found {len(files)} files:")
        for f in files:
            print(f"    - {f['filename']} ({f.get('type', 'unknown')})")

        # 3. 파일 다운로드
        print("\n[Step 3] Downloading files...")
        downloaded = []

        async def progress_callback(filename, downloaded_bytes, total_bytes):
            if total_bytes > 0:
                pct = (downloaded_bytes / total_bytes) * 100
                print(f"  ↓ {filename}: {downloaded_bytes:,} / {total_bytes:,} bytes ({pct:.1f}%)")

        import aiohttp
        async with aiohttp.ClientSession() as session:
            for f in files:
                try:
                    path = await wifi.download_file(
                        session,
                        "127.0.0.1:8001",
                        f["filename"],
                        str(download_dir),
                        progress_callback
                    )
                    downloaded.append(path)
                    print(f"  ✓ {f['filename']} → {path}")
                except Exception as e:
                    print(f"  ✗ {f['filename']}: {e}")

        # 결과
        print("\n[Result]")
        print(f"  ✓ Downloaded: {len(downloaded)}/{len(files)} files")
        print(f"  ✓ Location: {download_dir}")

        # 파일 검증
        for fpath in download_dir.glob("*"):
            size = fpath.stat().st_size
            print(f"    {fpath.name}: {size:,} bytes")

        print("\n" + "=" * 80)
        print("[Success] WiFi download pipeline working!")

    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(test_wifi_transfer())

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from core.ble_manager import BLEManager
from core.wifi_transfer import WiFiTransfer
from core.audio_pipeline import AudioPipeline
from core.transcription import TranscriptionService

# ── 글로벌 인스턴스 ───────────────────────────────────
ble   = BLEManager()
wifi  = WiFiTransfer()
audio = AudioPipeline()
stt   = TranscriptionService()

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
for sub in ["photo", "video", "audio", "misc"]:
    (DOWNLOAD_DIR / sub).mkdir(exist_ok=True)

# 알려진 안경 주소 저장 파일
KNOWN_DEVICE_FILE = Path("known_device.txt")


def _load_known_address() -> Optional[str]:
    if KNOWN_DEVICE_FILE.exists():
        addr = KNOWN_DEVICE_FILE.read_text(encoding="utf-8").strip()
        return addr if addr else None
    return None


def _save_known_address(address: str):
    KNOWN_DEVICE_FILE.write_text(address, encoding="utf-8")


# ── 자동 연결 백그라운드 태스크 ──────────────────────
async def _keepalive_loop():
    """BLE 연결 유지 — 15초마다 keepalive + 배터리 브로드캐스트"""
    while True:
        await asyncio.sleep(15)
        if ble.is_connected:
            try:
                await ble.get_battery()
                # 배터리 정보가 업데이트됐으면 브로드캐스트
                if ble.battery_level is not None:
                    await ws_mgr.broadcast({
                        "type": "ble_notify",
                        "battery": ble.battery_level,
                        "battery_charging": ble.battery_charging,
                        "media_count": ble.media_count,
                    })
                    # 배터리 15% 이하 경고
                    if ble.battery_level <= 15:
                        await ws_mgr.broadcast({
                            "type": "log",
                            "msg": f"⚠ 배터리 부족: {ble.battery_level}% — 충전 필요",
                            "level": "error"
                        })
            except Exception:
                pass


async def _auto_connect_loop():
    """서버 시작 시 M02C 자동 탐색 → 연결 → 사진 촬영"""
    await asyncio.sleep(2)

    while True:
        if ble.is_connected:
            await asyncio.sleep(5)
            continue

        # 주소 로드 (저장된 것 우선, 없으면 스캔으로 탐색)
        address = _load_known_address()

        if not address:
            await ws_mgr.broadcast({"type": "log", "msg": "M02C 스캔 중...", "level": "info"})
            try:
                address = await _scan_for_glasses()
            except Exception:
                await asyncio.sleep(10)
                continue
            if not address:
                await ws_mgr.broadcast({"type": "log", "msg": "안경 못 찾음 — 10초 후 재스캔", "level": "warn"})
                await asyncio.sleep(10)
                continue

        await ws_mgr.broadcast({"type": "log", "msg": f"자동 연결 중: {address}", "level": "info"})
        try:
            # 연결 재시도 (서비스 검색 타임아웃 대응)
            connected = False
            for attempt in range(3):
                try:
                    await ble.connect(address, on_notify=_notify_callback)
                    connected = True
                    break
                except Exception as e:
                    if attempt < 2:
                        await ws_mgr.broadcast({"type": "log", "msg": f"연결 시도 {attempt+1}/3 실패, 재시도...", "level": "warn"})
                        await asyncio.sleep(3)
                    else:
                        raise

            if not connected:
                raise RuntimeError("3회 연결 실패")

            _save_known_address(address)
            await ws_mgr.broadcast({"type": "ble_connected", **ble.to_dict()})
            await ws_mgr.broadcast({"type": "log", "msg": f"연결 성공! 배터리: {ble.battery_level}%", "level": "ok"})

        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:80]}"
            await ws_mgr.broadcast({"type": "log", "msg": f"연결 실패: {err} — 10초 후 재시도", "level": "warn"})
            await asyncio.sleep(10)


async def _scan_for_glasses() -> Optional[str]:
    """M02C* / HeyCyan 안경 자동 탐색"""
    from bleak import BleakScanner
    await ws_mgr.broadcast({"type": "log", "msg": "BLE 스캔 중 (15초)...", "level": "info"})
    devices = await BleakScanner.discover(timeout=15.0)
    for d in devices:
        name = (d.name or "").lower()
        # M0xxxx 패턴 (M02C_EB50, M08C_XXXX 등)
        if name.startswith("m0"):
            await ws_mgr.broadcast({"type": "log", "msg": f"안경 발견: {d.name} ({d.address})", "level": "ok"})
            return d.address
        if name.startswith("g") or "cyan" in name:
            await ws_mgr.broadcast({"type": "log", "msg": f"안경 후보: {d.name} ({d.address})", "level": "ok"})
            return d.address
    await ws_mgr.broadcast({"type": "log", "msg": f"스캔 완료 - M02C 못 찾음", "level": "warn"})
    return None


@asynccontextmanager
async def lifespan(app_):
    asyncio.create_task(_auto_connect_loop())
    asyncio.create_task(_keepalive_loop())
    yield
    await ble.disconnect()


# ── FastAPI 앱 ────────────────────────────────────────
app = FastAPI(title="HeyCyan Admin", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/files",  StaticFiles(directory="downloads"), name="files")


# ── WebSocket 브로드캐스터 ────────────────────────────
class WSManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        self._clients.discard(ws) if hasattr(self._clients, 'discard') else \
            self._clients.remove(ws) if ws in self._clients else None

    async def broadcast(self, data: dict):
        msg = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._clients:
                self._clients.remove(ws)


ws_mgr = WSManager()


# ── 유틸 ─────────────────────────────────────────────
def _notify_callback(data: bytes):
    asyncio.create_task(ws_mgr.broadcast({
        "type": "ble_notify",
        "hex": data.hex(),
        "battery": ble.battery_level,
        "battery_charging": ble.battery_charging,
        "media_count": ble.media_count,
        "wifi_ip": ble.extracted_ip,
        "wifi_ssid": ble._wifi_ssid,
    }))


# ── 라우트: 페이지 ────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("static/index.html")


# ── 라우트: 상태 ──────────────────────────────────────
@app.get("/api/status")
async def get_status():
    return {
        **ble.to_dict(),
        "wifi_ip": wifi.glasses_ip or ble.extracted_ip,
        "download_dir": str(DOWNLOAD_DIR.resolve()),
    }


# ── 라우트: BLE ───────────────────────────────────────
@app.post("/api/ble/scan")
async def scan():
    devices = await ble.scan()
    return {"devices": [{"name": d.name or "Unknown", "address": d.address} for d in devices]}


@app.post("/api/ble/connect/{address}")
async def connect(address: str):
    try:
        await ble.connect(address, on_notify=_notify_callback)
        _save_known_address(address)  # 성공한 주소 저장 → 다음 시작 시 자동 연결
        await ws_mgr.broadcast({"type": "ble_connected", **ble.to_dict()})
        return ble.to_dict()
    except Exception as e:
        await ws_mgr.broadcast({"type": "ble_disconnected"})
        raise HTTPException(400, f"BLE 연결 실패: {type(e).__name__}: {repr(e)}")


@app.post("/api/ble/disconnect")
async def disconnect():
    await ble.disconnect()
    await ws_mgr.broadcast({"type": "ble_disconnected"})
    return {"ok": True}


@app.get("/api/ble/uuids")
async def get_uuids():
    result = await ble.print_all_uuids()
    # 브라우저 로그에도 출력
    for svc in result:
        await ws_mgr.broadcast({"type": "log", "msg": f"[SVC] {svc['uuid']}", "level": "info"})
        for ch in svc.get("chars", []):
            await ws_mgr.broadcast({
                "type": "log",
                "msg": f"  └ {ch['uuid']} [{','.join(ch['properties'])}]",
                "level": "ok" if any(p in ch['properties'] for p in ['write','notify']) else "info"
            })
    return result


# ── 라우트: 기기 제어 ─────────────────────────────────
@app.post("/api/device/photo")
async def take_photo():
    await ble.take_photo()
    return {"ok": True}


@app.post("/api/device/video/start")
async def start_video():
    await ble.start_video()
    return {"ok": True}


@app.post("/api/device/video/stop")
async def stop_video():
    await ble.stop_video()
    return {"ok": True}


@app.post("/api/device/audio/start")
async def start_audio():
    await ble.start_audio()
    return {"ok": True}


@app.post("/api/device/audio/stop")
async def stop_audio():
    await ble.stop_audio()
    return {"ok": True}


@app.post("/api/device/ai_photo")
async def ai_photo():
    await ble.trigger_ai_photo()
    return {"ok": True}


@app.post("/api/device/battery")
async def refresh_battery():
    await ble.get_battery()
    return {"battery": ble.battery_level}


# ── 라우트: Wi-Fi 싱크 ────────────────────────────────
GLASSES_WIFI_PASSWORD = "123456789"  # iOS 소스 GlassesWiFiHandler.m 확인

@app.post("/api/wifi/enable")
async def enable_wifi():
    """Wi-Fi 핫스팟 활성화 → SSID 수신 → Windows Wi-Fi 자동 연결"""
    await ws_mgr.broadcast({"type": "log", "msg": "Wi-Fi 핫스팟 활성화 중...", "level": "info"})
    ssid_or_ip = await ble.enable_wifi_transfer()

    if ssid_or_ip:
        wifi.glasses_ip = ssid_or_ip if "." in ssid_or_ip else None
        # Windows Wi-Fi 자동 연결 시도
        ssid = ble._wifi_ssid or ssid_or_ip
        if ssid and "." not in ssid:
            result = await _connect_windows_wifi(ssid, GLASSES_WIFI_PASSWORD)
            await ws_mgr.broadcast({"type": "log", "msg": f"Wi-Fi 연결: {result}", "level": "ok" if "성공" in result else "warn"})

    return {"ip": wifi.glasses_ip, "ssid": ble._wifi_ssid, "raw": ssid_or_ip}


async def _connect_windows_wifi(ssid: str, password: str) -> str:
    """netsh로 Windows Wi-Fi 자동 연결 (WPA2 + Open 모두 시도)"""
    import subprocess

    profile_path = DOWNLOAD_DIR / "glasses_wifi.xml"

    # WPA2 프로필
    profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM><security>
        <authEncryption><authentication>WPA2PSK</authentication>
        <encryption>AES</encryption></authEncryption>
        <sharedKey><keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{password}</keyMaterial></sharedKey>
    </security></MSM>
</WLANProfile>"""

    profile_path.write_text(profile_xml, encoding="utf-8")

    try:
        subprocess.run(
            ["netsh", "wlan", "add", "profile", f"filename={profile_path}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        r = subprocess.run(
            ["netsh", "wlan", "connect", f"name={ssid}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if "성공" in r.stdout or "successfully" in r.stdout.lower():
            # 연결 후 IP 자동 탐색
            await asyncio.sleep(3)
            discovered = await wifi.discover_ip()
            if discovered:
                wifi.glasses_ip = discovered
                return f"Wi-Fi 연결 성공: {ssid} / IP: {discovered}"
            return f"Wi-Fi 연결 성공: {ssid} (IP 탐색 중)"
        return f"연결 시도 중: {ssid}"
    except Exception as e:
        return f"연결 실패: {e}"


@app.post("/api/wifi/probe")
async def probe_endpoints():
    ip = wifi.glasses_ip or ble.extracted_ip
    if not ip:
        raise HTTPException(400, "안경 IP 없음. Wi-Fi 활성화 먼저 실행하세요.")
    results = await wifi.probe_endpoints(ip)
    return results


@app.post("/api/wifi/sync")
async def sync_files():
    # BLE 없이도 Wi-Fi 직접 연결 시 싱크 가능
    ip = wifi.glasses_ip or ble.extracted_ip
    if not ip:
        # IP 자동 탐색 시도
        ip = await wifi.discover_ip()
    if not ip:
        raise HTTPException(400,
            "안경 IP를 찾을 수 없습니다. 순서: ① BLE 연결 → ② 핫스팟 활성화 → "
            "③ PC Wi-Fi를 안경 핫스팟에 연결 → ④ 싱크")

    async def progress_cb(filename: str, downloaded: int, total: int):
        pct = round(downloaded / total * 100) if total > 0 else 0
        await ws_mgr.broadcast({
            "type": "sync_progress",
            "filename": filename,
            "downloaded": downloaded,
            "total": total,
            "pct": pct,
        })

    try:
        files = await wifi.sync_all(
            save_dir=str(DOWNLOAD_DIR),
            hint_ip=ip,
            progress_cb=progress_cb,
        )
    except Exception as e:
        raise HTTPException(500, f"싱크 실패: {str(e)}")

    await ws_mgr.broadcast({"type": "sync_complete", "count": len(files)})
    return {"files": files, "count": len(files)}


# ── 라우트: 미디어 목록 ───────────────────────────────
@app.get("/api/media/list")
async def list_media():
    result = {"photos": [], "videos": [], "audio": []}
    for f in DOWNLOAD_DIR.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(DOWNLOAD_DIR)).replace("\\", "/")
        info = {
            "name": f.name,
            "path": rel,
            "url":  f"/files/{rel}",
            "size": f.stat().st_size,
        }
        ext = f.suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".heic"}:
            result["photos"].append(info)
        elif ext in {".mp4", ".mov", ".m4v"}:
            result["videos"].append(info)
        elif ext in {".opus", ".wav", ".ogg", ".m4a"}:
            result["audio"].append(info)
    return result


# ── 라우트: 오디오 처리 ───────────────────────────────
@app.post("/api/audio/convert/{filename:path}")
async def convert_audio(filename: str):
    src = DOWNLOAD_DIR / filename
    if not src.exists():
        raise HTTPException(404, "파일 없음")
    wav = await audio.opus_to_wav(str(src))
    rel = str(Path(wav).relative_to(DOWNLOAD_DIR)).replace("\\", "/")
    return {"wav_path": rel, "url": f"/files/{rel}"}


@app.post("/api/audio/transcribe/{filename:path}")
async def transcribe_audio(
    filename: str,
    lang: str = "ko",
    use_cloud: bool = False,
    chunks: bool = False,
):
    src = DOWNLOAD_DIR / filename
    if not src.exists():
        raise HTTPException(404, "파일 없음")

    async def on_progress(i, total, stage):
        await ws_mgr.broadcast({"type": "stt_progress", "chunk": i, "total": total, "stage": stage})

    if chunks:
        result = await stt.transcribe_chunks(str(src), lang=lang, on_progress=on_progress)
    else:
        result = await stt.transcribe(str(src), lang=lang, use_cloud=use_cloud)

    await ws_mgr.broadcast({"type": "stt_result", "file": filename, "text": result["text"]})
    return result


@app.post("/api/audio/batch_convert")
async def batch_convert():
    wavs = await audio.batch_convert(str(DOWNLOAD_DIR / "audio"))
    return {"converted": len(wavs), "files": wavs}


# ── WebSocket ─────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_mgr.connect(websocket)
    try:
        # 연결 즉시 현재 상태 전송
        await websocket.send_text(json.dumps({"type": "init", **ble.to_dict()}))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_mgr.disconnect(websocket)


# ── 실행 ──────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

"""
사진 + 음성 + 영상 촬영 후 Wi-Fi로 전체 다운로드
"""
import asyncio, sys, subprocess, re, aiohttp, aiofiles
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY_UUID = "de5bf729-d711-4e47-af26-65e3012a5dc7"
DOWNLOAD_DIR = Path("downloads")
for sub in ["photo", "video", "audio"]:
    (DOWNLOAD_DIR / sub).mkdir(parents=True, exist_ok=True)


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        crc &= 0xFFFF
    return crc


def build(payload, cmd=0x41):
    p = bytes(payload)
    return bytes([0xBC, cmd]) + len(p).to_bytes(2, "little") + crc16(p).to_bytes(2, "little") + p


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def parse_wifi_creds(data):
    if len(data) < 10 or data[0] != 0xBC:
        return None, None
    ln = int.from_bytes(data[2:4], "little")
    payload = data[6:6+ln]
    if len(payload) < 12:
        return None, None
    try:
        idx = 4
        ssid_len = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
        pw_len   = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
        ssid = payload[idx:idx+ssid_len].decode("ascii", errors="replace"); idx += ssid_len
        pw   = payload[idx:idx+pw_len].decode("ascii", errors="replace")
        if 3 <= len(ssid) <= 32 and all(c.isprintable() for c in ssid):
            return ssid, pw
    except Exception:
        pass
    return None, None


def connect_wifi(ssid, pw):
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>
<MSM><security><authEncryption><authentication>WPA2PSK</authentication>
<encryption>AES</encryption></authEncryption><sharedKey><keyType>passPhrase</keyType>
<protected>false</protected><keyMaterial>{pw}</keyMaterial>
</sharedKey></security></MSM></WLANProfile>"""
    Path("glasses_wifi.xml").write_text(profile, encoding="utf-8")
    subprocess.run(["netsh", "wlan", "add", "profile", "filename=glasses_wifi.xml"], capture_output=True)
    r = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "successfully" in r.stdout.lower() or "성공" in r.stdout


async def find_ip():
    ips = ["192.168.31.1", "192.168.49.1", "192.168.43.1", "192.168.4.1", "10.0.0.1"]
    async def test(ip):
        try:
            async with aiohttp.ClientSession() as s:
                for path in ["/files/media.config", "/manifest.json"]:
                    try:
                        async with s.get(f"http://{ip}{path}", timeout=aiohttp.ClientTimeout(total=2)) as r:
                            if r.status == 200:
                                return ip, path, await r.text()
                    except: pass
        except: pass
    results = await asyncio.gather(*[test(ip) for ip in ips])
    return next((r for r in results if r), None)


def classify(fname):
    ext = Path(fname).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".heic"}: return "photo"
    if ext in {".mp4", ".mov", ".m4v"}:           return "video"
    if ext in {".opus", ".wav", ".ogg", ".m4a"}:  return "audio"
    return "misc"


async def download_all(ip, files):
    downloaded = {"photo": [], "video": [], "audio": []}
    async with aiohttp.ClientSession() as s:
        for fname in files:
            cat = classify(fname)
            save = DOWNLOAD_DIR / cat / fname
            if save.exists():
                downloaded[cat].append(str(save)); continue
            try:
                async with s.get(f"http://{ip}/files/{fname}",
                                  timeout=aiohttp.ClientTimeout(total=120)) as r:
                    r.raise_for_status()
                    async with aiofiles.open(save, "wb") as f:
                        async for chunk in r.content.iter_chunked(8192):
                            await f.write(chunk)
                size_kb = save.stat().st_size // 1024
                print(f"[{ts()}]   [{cat}] {fname} ({size_kb}KB)")
                downloaded[cat].append(str(save))
            except Exception as e:
                print(f"[{ts()}]   X {fname}: {e}")
    return downloaded


async def main():
    print(f"[{ts()}] ============================")
    print(f"[{ts()}]  사진 + 음성 + 영상 전체 테스트")
    print(f"[{ts()}] ============================\n")

    # ── BLE 연결 ────────────────────────────────────
    devices = await BleakScanner.discover(timeout=10.0)
    dev = next((d for d in devices if (d.name or "").upper().startswith("M0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"[{ts()}] O {dev.name}")

    client = None
    for i in range(4):
        try:
            c = BleakClient(dev.address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            client = c; break
        except Exception as e:
            print(f"[{ts()}] 연결 {i+1}/4: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)
    if not client: print("X 연결 실패"); return
    print(f"[{ts()}] O 연결! MTU={client.mtu_size}\n")

    wifi_creds = [None, None]

    def h(s, d):
        raw = bytes(d)
        ssid, pw = parse_wifi_creds(raw)
        if ssid:
            wifi_creds[0] = ssid
            wifi_creds[1] = pw
            print(f"[{ts()}] ★ Wi-Fi: {ssid} / {pw}")

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)

    try:
        # ── 1. 사진 촬영 ───────────────────────────
        print(f"[{ts()}] [1] 사진 촬영...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x01]), response=False)
        await asyncio.sleep(3.0)
        print(f"[{ts()}]   O 사진 촬영 완료\n")

        # ── 2. 음성 녹음 (5초) ─────────────────────
        print(f"[{ts()}] [2] 음성 녹음 시작...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x08]), response=False)
        await asyncio.sleep(5.0)
        print(f"[{ts()}]   O 5초 녹음 중지...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x0C]), response=False)
        await asyncio.sleep(2.0)
        print(f"[{ts()}]   O 음성 녹음 완료\n")

        # ── 3. 영상 녹화 (5초) ─────────────────────
        print(f"[{ts()}] [3] 영상 녹화 시작...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x02]), response=False)
        await asyncio.sleep(5.0)
        print(f"[{ts()}]   O 5초 녹화 중지...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x03]), response=False)
        await asyncio.sleep(2.0)
        print(f"[{ts()}]   O 영상 녹화 완료\n")

        # ── 4. Wi-Fi 전송 모드 ─────────────────────
        print(f"[{ts()}] [4] Wi-Fi 전송 모드 진입...")
        await client.write_gatt_char(WRITE_UUID, build([0x02, 0x01, 0x04, 0x02]), response=False)

        for i in range(15):
            await asyncio.sleep(1.0)
            if wifi_creds[0]: break
            if (i+1) % 5 == 0: print(f"  {i+1}초 대기...")

        if not wifi_creds[0]:
            print(f"[{ts()}] X SSID 수신 안 됨"); return

        ssid, pw = wifi_creds
        print(f"[{ts()}]   SSID={ssid}\n")

        # ── 5. Windows Wi-Fi 연결 ──────────────────
        print(f"[{ts()}] [5] Wi-Fi 연결 중...")
        glasses_ip = None
        for attempt in range(6):
            connect_wifi(ssid, pw)
            await asyncio.sleep(5.0)
            result = await find_ip()
            if result:
                glasses_ip, path, content = result
                print(f"[{ts()}]   O IP={glasses_ip}")
                break
            print(f"  시도 {attempt+1}/6...")

        if not glasses_ip:
            print(f"[{ts()}] X HTTP 연결 실패"); return

        # ── 6. 파일 목록 + 다운로드 ────────────────
        if "media.config" in path:
            files = [l.strip() for l in content.splitlines() if l.strip()]
        else:
            import json
            files = [f["filename"] for f in json.loads(content).get("files", [])]

        print(f"\n[{ts()}] [6] 파일 {len(files)}개 다운로드...")
        downloaded = await download_all(glasses_ip, files)

        # ── 결과 ──────────────────────────────────
        print(f"\n[{ts()}] ============================")
        print(f"[{ts()}]  다운로드 완료!")
        print(f"[{ts()}]  사진: {len(downloaded['photo'])}개")
        print(f"[{ts()}]  영상: {len(downloaded['video'])}개")
        print(f"[{ts()}]  음성: {len(downloaded['audio'])}개")
        print(f"[{ts()}]  저장: {DOWNLOAD_DIR.resolve()}")
        print(f"[{ts()}] ============================")

    except Exception as e:
        print(f"오류: {e}")
        import traceback; traceback.print_exc()
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()

asyncio.run(main())

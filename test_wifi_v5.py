"""
v5 프로토콜 Wi-Fi 파일 전송 테스트
1. BLE AP 모드 명령 → 핫스팟 생성
2. Windows Wi-Fi 자동 연결
3. HTTP 파일 다운로드
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
WIFI_PASS   = "123456789"
DOWNLOAD_DIR = Path("downloads/photo")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

CANDIDATE_IPS = ["192.168.49.1", "192.168.43.1", "192.168.4.1", "192.168.1.1", "10.0.0.1"]


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
    return bytes([0xBC, cmd]) + len(p).to_bytes(2,"little") + crc16(p).to_bytes(2,"little") + p


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()


async def connect_wifi(ssid, password):
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>
<MSM><security><authEncryption>
<authentication>WPA2PSK</authentication><encryption>AES</encryption>
</authEncryption><sharedKey><keyType>passPhrase</keyType>
<protected>false</protected><keyMaterial>{password}</keyMaterial>
</sharedKey></security></MSM></WLANProfile>"""
    Path("glasses_wifi.xml").write_text(profile, encoding="utf-8")
    subprocess.run(["netsh","wlan","add","profile","filename=glasses_wifi.xml"],
                   capture_output=True)
    r = subprocess.run(["netsh","wlan","connect",f"name={ssid}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "성공" in r.stdout or "successfully" in r.stdout.lower()


async def find_glasses_ip(timeout=3.0):
    async def test(ip):
        try:
            async with aiohttp.ClientSession() as s:
                for path in ["/files/media.config", "/manifest.json"]:
                    try:
                        async with s.get(f"http://{ip}{path}",
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                            if r.status == 200:
                                return ip
                    except: pass
        except: pass
    results = await asyncio.gather(*[test(ip) for ip in CANDIDATE_IPS])
    return next((r for r in results if r), None)


async def download_files(ip):
    files = []
    async with aiohttp.ClientSession() as s:
        # media.config 시도
        try:
            async with s.get(f"http://{ip}/files/media.config", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    text = await r.text()
                    files = [l.strip() for l in text.splitlines() if l.strip()]
                    print(f"[{ts()}] media.config: {len(files)}개 파일")
        except: pass

        if not files:
            try:
                async with s.get(f"http://{ip}/manifest.json", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        files = [f["filename"] for f in data.get("files",[])]
            except: pass

        if not files:
            print(f"[{ts()}] X 파일 목록 없음"); return []

        downloaded = []
        for fname in files:
            save_path = DOWNLOAD_DIR / fname
            if save_path.exists():
                print(f"[{ts()}] 이미 있음: {fname}")
                downloaded.append(str(save_path)); continue
            try:
                async with s.get(f"http://{ip}/files/{fname}",
                                  timeout=aiohttp.ClientTimeout(total=60)) as r:
                    r.raise_for_status()
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in r.content.iter_chunked(8192):
                            await f.write(chunk)
                print(f"[{ts()}] O {fname} ({save_path.stat().st_size//1024}KB)")
                downloaded.append(str(save_path))
            except Exception as e:
                print(f"[{ts()}] X {fname}: {e}")
        return downloaded


async def main():
    print(f"[{ts()}] Wi-Fi 파일 전송 테스트\n")

    # BLE 연결
    devices = await BleakScanner.discover(timeout=10.0)
    dev = next((d for d in devices if (d.name or "").upper().startswith("M0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"O {dev.name}")

    client = None
    for i in range(4):
        try:
            c = BleakClient(dev.address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            client = c; break
        except Exception as e:
            print(f"연결 {i+1}/4: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)

    if not client: print("X 연결 실패"); return
    print(f"O 연결! MTU={client.mtu_size}\n")

    rx = []
    found_ip = [None]
    def h(s, d):
        raw = bytes(d)
        rx.append(raw)
        txt = raw.decode("utf-8", errors="ignore")
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', txt)
        if m: found_ip[0] = m.group(1)

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)
    print(f"O Notify 구독\n")

    before = get_ssids()

    try:
        # Wi-Fi AP 모드 활성화
        ap_pkt = build([0x02, 0x01, 0x04, 0x02])  # AP Album 가져오기
        print(f"[{ts()}] Wi-Fi AP 모드: {ap_pkt.hex(' ').upper()}")
        await client.write_gatt_char(WRITE_UUID, ap_pkt, response=False)

        # SSID 대기 (20초)
        print(f"[{ts()}] SSID 탐색 중 (20초)...")
        ssid = None
        for i in range(20):
            await asyncio.sleep(1.0)
            new = get_ssids() - before
            if new:
                ssid = list(new)[0]
                print(f"[{ts()}] O 새 SSID: {ssid}")
                break
            if found_ip[0]:
                print(f"[{ts()}] O BLE IP: {found_ip[0]}")
                break
            if (i+1) % 5 == 0: print(f"  {i+1}초...")

        # Wi-Fi 연결
        glasses_ip = found_ip[0]
        if ssid and not glasses_ip:
            print(f"[{ts()}] Wi-Fi 연결: {ssid} (비밀번호: {WIFI_PASS})")
            ok = await connect_wifi(ssid, WIFI_PASS)
            print(f"[{ts()}] {'O' if ok else '?'} 연결 {'성공' if ok else '시도 중'}")
            await asyncio.sleep(5.0)
            glasses_ip = await find_glasses_ip()
            if glasses_ip:
                print(f"[{ts()}] O 안경 IP: {glasses_ip}")

        if not glasses_ip:
            print(f"[{ts()}] X IP 없음 — 수동으로 Wi-Fi 연결 후 IP 확인 필요")
            return

        # 파일 다운로드
        print(f"\n[{ts()}] 파일 다운로드 시작: http://{glasses_ip}/")
        files = await download_files(glasses_ip)
        print(f"\n[{ts()}] 완료: {len(files)}개 다운로드")
        print(f"저장 위치: {DOWNLOAD_DIR.resolve()}")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()

asyncio.run(main())

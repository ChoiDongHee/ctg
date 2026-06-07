"""
v5 완전 Wi-Fi 전송 플로우
BLE 연결 유지 + AP 명령 → SSID/PW 파싱 → Wi-Fi 연결 → HTTP 다운로드
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
DOWNLOAD_DIR = Path("downloads/photo")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


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


def parse_wifi_creds(data: bytes):
    """
    BLE 응답에서 SSID + Password 추출
    실제 형식: BC 41 [LEN] [CRC] [cmd_echo 4B] [SSID_LEN 2B LE] [PW_LEN 2B LE] [SSID] [PW]
    """
    if len(data) < 10 or data[0] != 0xBC:
        return None, None
    ln = int.from_bytes(data[2:4], "little")
    payload = data[6:6+ln]
    if len(payload) < 12:
        return None, None
    try:
        idx = 4  # cmd_echo 4바이트 건너뜀
        ssid_len = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
        pw_len   = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
        ssid = payload[idx:idx+ssid_len].decode("ascii", errors="replace"); idx += ssid_len
        pw   = payload[idx:idx+pw_len].decode("ascii", errors="replace")
        if 3 <= len(ssid) <= 32 and all(c.isprintable() for c in ssid):
            return ssid, pw
    except Exception:
        pass
    return None, None


def connect_wifi(ssid, password):
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>
<MSM><security><authEncryption><authentication>WPA2PSK</authentication>
<encryption>AES</encryption></authEncryption><sharedKey><keyType>passPhrase</keyType>
<protected>false</protected><keyMaterial>{password}</keyMaterial>
</sharedKey></security></MSM></WLANProfile>"""
    Path("glasses_wifi.xml").write_text(profile, encoding="utf-8")
    subprocess.run(["netsh","wlan","add","profile","filename=glasses_wifi.xml"], capture_output=True)
    r = subprocess.run(["netsh","wlan","connect",f"name={ssid}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


async def find_ip(timeout=2.0):
    ips = ["192.168.31.1","192.168.49.1","192.168.49.79","192.168.43.1","192.168.4.1","10.0.0.1","192.168.0.1"]
    async def test(ip):
        try:
            async with aiohttp.ClientSession() as s:
                for p in ["/files/media.config", "/manifest.json"]:
                    try:
                        async with s.get(f"http://{ip}{p}", timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                            if r.status == 200:
                                return ip, p, await r.text()
                    except: pass
        except: pass
    results = await asyncio.gather(*[test(ip) for ip in ips])
    return next((r for r in results if r), None)


async def download_all(ip, files):
    downloaded = []
    async with aiohttp.ClientSession() as s:
        for fname in files:
            save = DOWNLOAD_DIR / fname
            if save.exists():
                print(f"[{ts()}]   이미 있음: {fname}"); downloaded.append(str(save)); continue
            try:
                async with s.get(f"http://{ip}/files/{fname}", timeout=aiohttp.ClientTimeout(total=60)) as r:
                    r.raise_for_status()
                    async with aiofiles.open(save, "wb") as f:
                        async for chunk in r.content.iter_chunked(8192):
                            await f.write(chunk)
                print(f"[{ts()}]   O {fname} ({save.stat().st_size//1024}KB)")
                downloaded.append(str(save))
            except Exception as e:
                print(f"[{ts()}]   X {fname}: {e}")
    return downloaded


async def main():
    print(f"[{ts()}] v5 Wi-Fi 완전 전송 플로우\n")

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

    wifi_creds = [None, None]  # [ssid, password]

    def h(s, d):
        raw = bytes(d)
        ssid, pw = parse_wifi_creds(raw)
        if ssid:
            wifi_creds[0] = ssid
            wifi_creds[1] = pw
            print(f"[{ts()}] ★ SSID={ssid!r} PW={pw!r}")
        else:
            ln = int.from_bytes(raw[2:4],"little") if len(raw)>=4 and raw[0]==0xBC else 0
            pay = raw[6:6+ln] if len(raw)>=6+ln else raw[6:]
            print(f"[{ts()}] << CMD=0x{raw[1]:02X} payload={pay.hex(' ').upper()[:40]}")

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)
    print(f"O Notify 구독\n")

    try:
        # AP 명령 전송
        pkt = build([0x02, 0x01, 0x04, 0x02])
        print(f"[{ts()}] AP 명령: {pkt.hex(' ').upper()}")
        await client.write_gatt_char(WRITE_UUID, pkt, response=False)

        # SSID 수신 대기
        for i in range(15):
            await asyncio.sleep(1.0)
            if wifi_creds[0]: break
            if (i+1) % 5 == 0: print(f"  {i+1}초 대기...")

        if not wifi_creds[0]:
            print(f"[{ts()}] X SSID 수신 안 됨")
            return

        ssid, pw = wifi_creds
        print(f"\n[{ts()}] Wi-Fi 연결: {ssid} ({pw})")

        # 여러 번 연결 시도 (핫스팟 준비 시간 필요)
        glasses_ip = None
        for attempt in range(6):
            r = connect_wifi(ssid, pw)
            print(f"  시도 {attempt+1}: {r.strip()[:50]}")
            await asyncio.sleep(5.0)

            result = await find_ip()
            if result:
                glasses_ip, path, content = result
                print(f"[{ts()}] ★ HTTP 연결: {glasses_ip}{path}")
                print(f"  내용: {content[:100]}")
                break
            print(f"  IP 탐색 중...")

        if not glasses_ip:
            print(f"[{ts()}] X HTTP 연결 실패")
            return

        # 파일 목록 파싱
        if "media.config" in path:
            files = [l.strip() for l in content.splitlines() if l.strip()]
        else:
            import json
            data = json.loads(content)
            files = [f["filename"] for f in data.get("files",[])]

        print(f"\n[{ts()}] 파일 {len(files)}개 다운로드 시작...")
        downloaded = await download_all(glasses_ip, files)
        print(f"\n[{ts()}] ★ 완료: {len(downloaded)}개")
        print(f"저장: {DOWNLOAD_DIR.resolve()}")

    except Exception as e:
        print(f"오류: {e}")
        import traceback; traceback.print_exc()
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()
        print(f"\n[{ts()}] 종료")

asyncio.run(main())

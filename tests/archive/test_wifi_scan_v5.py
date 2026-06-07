"""
v5: Wi-Fi AP 명령 후 SSID 탐색 + 자동 연결 시도
"""
import asyncio, sys, subprocess, re, time
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY_UUID = "de5bf729-d711-4e47-af26-65e3012a5dc7"
WIFI_PASS   = "123456789"

WORK_TYPE = {0:"대기",1:"사진",2:"영상",3:"영상중지",4:"전송",5:"OTA",6:"AI사진",7:"AI대화",8:"오디오"}


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


def parse_resp(data):
    if len(data) < 7 or data[0] != 0xBC: return data.hex(' ').upper()
    cmd = data[1]
    ln = int.from_bytes(data[2:4], "little")
    payload = data[6:6+ln]
    mode = WORK_TYPE.get(payload[0], f"0x{payload[0]:02X}") if payload else "?"
    return f"CMD=0x{cmd:02X} mode={mode} payload={payload.hex(' ').upper()}"


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def get_all_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()


async def try_http(ip, timeout=2.0):
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            for path in ["/files/media.config", "/manifest.json"]:
                try:
                    async with s.get(f"http://{ip}{path}",
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                        if r.status == 200:
                            content = await r.text()
                            return ip, path, content[:200]
                except: pass
    except: pass
    return None


async def main():
    print(f"[{ts()}] Wi-Fi AP 명령 + SSID 탐색\n")

    devices = await BleakScanner.discover(timeout=10.0)
    dev = next((d for d in devices if (d.name or "").upper().startswith("M0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"O {dev.name}\n")

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
    print(f"O 연결! MTU={client.mtu_size}")

    found_ip = [None]
    def h(s, d):
        raw = bytes(d)
        txt = raw.decode("utf-8", errors="ignore")
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', txt)
        if m: found_ip[0] = m.group(1)
        parsed = parse_resp(raw)
        print(f"[{ts()}] << {parsed}")

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)
    print(f"O Notify 구독\n")

    before = get_all_ssids()
    print(f"현재 Wi-Fi: {sorted(before)}\n")

    try:
        # AP 앨범 명령
        pkt = build([0x02, 0x01, 0x04, 0x02])
        print(f"[{ts()}] AP 모드 명령: {pkt.hex(' ').upper()}")
        await client.write_gatt_char(WRITE_UUID, pkt, response=False)

        # 30초간 SSID 감시
        print(f"[{ts()}] 30초 SSID 감시...\n")
        new_ssids = set()
        for i in range(30):
            await asyncio.sleep(1.0)
            current = get_all_ssids()
            diff = current - before

            if diff - new_ssids:
                new_ssids |= diff
                print(f"[{ts()}] ★ 새 SSID: {diff}")
                # 바로 연결 시도
                for ssid in diff:
                    print(f"  → {ssid} 연결 시도 (비번: {WIFI_PASS})")
                    try:
                        profile = f"""<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
<name>{ssid}</name><SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>
<MSM><security><authEncryption><authentication>WPA2PSK</authentication>
<encryption>AES</encryption></authEncryption><sharedKey><keyType>passPhrase</keyType>
<protected>false</protected><keyMaterial>{WIFI_PASS}</keyMaterial>
</sharedKey></security></MSM></WLANProfile>"""
                        open("glasses_wifi.xml","w").write(profile)
                        subprocess.run(["netsh","wlan","add","profile","filename=glasses_wifi.xml"],capture_output=True)
                        r = subprocess.run(["netsh","wlan","connect",f"name={ssid}"],
                                           capture_output=True, text=True, encoding="utf-8", errors="replace")
                        print(f"  결과: {r.stdout.strip()[:60]}")
                    except Exception as e:
                        print(f"  오류: {e}")

            if found_ip[0]:
                print(f"[{ts()}] ★ BLE IP 수신: {found_ip[0]}")

            if (i+1) % 5 == 0:
                print(f"  {i+1}초... SSID변화: {new_ssids or '없음'}")

        # HTTP 연결 확인
        print(f"\n[{ts()}] HTTP 탐색...")
        ips = ["192.168.49.1","192.168.49.79","192.168.43.1","192.168.4.1"] + (
            [found_ip[0]] if found_ip[0] else [])
        results = await asyncio.gather(*[try_http(ip) for ip in ips])
        for r in results:
            if r:
                print(f"[{ts()}] ★ HTTP 발견: {r[0]}{r[1]}")
                print(f"  내용: {r[2]}")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()
        print(f"\n[{ts()}] 종료")

asyncio.run(main())

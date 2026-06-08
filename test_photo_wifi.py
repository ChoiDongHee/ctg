"""
사진 촬영 → WiFi 다운로드 테스트
PCAPdroid 패킷 기반 구현:
  #1: GET /files/media.config (391B)
  #2: GET /files/<photo>      (~1MB)
  #3: GET /files/<photo>...   (~5MB)
  #4: 종료 확인               (3.7KB)
"""
import asyncio
import subprocess
import sys
import io
import time
import socket
import aiohttp
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.ble_manager import BLEManager
from core.wifi_transfer import WiFiTransfer

DEVICE_ADDR   = "3C:A6:DE:5B:EB:50"
GLASSES_SSID  = "WF1_686725cd4952"
GLASSES_IP    = "192.168.49.64"  # PCAPdroid에서 확인
DOWNLOAD_DIR  = Path("downloads/photos")


# ── WiFi 유틸 ─────────────────────────────────────────────
def _netsh(*args) -> str:
    r = subprocess.run(["netsh"] + list(args), capture_output=True)
    return (r.stdout or b"").decode("cp949", errors="replace").strip()

def _current_ssid() -> str:
    for line in _netsh("wlan", "show", "interfaces").splitlines():
        if "SSID" in line and "BSSID" not in line:
            return line.split(":", 1)[-1].strip()
    return ""

def _scan_ssids() -> list:
    out = _netsh("wlan", "show", "networks")
    return [l.split(":", 1)[-1].strip()
            for l in out.splitlines()
            if l.strip().startswith("SSID") and "BSSID" not in l and l.split(":",1)[-1].strip()]

def _http_ok(ip: str, timeout=2.0) -> bool:
    try:
        s = socket.create_connection((ip, 80), timeout=timeout)
        s.close()
        return True
    except:
        return False

def _wifi_connect(ssid: str, password: str, prev: str) -> bool:
    """disconnect → 프로필 등록 → connect → IP 대역 확인"""
    _netsh("wlan", "disconnect")
    time.sleep(1)

    hex_ssid = ''.join(f'{ord(c):02x}' for c in ssid)
    if password:
        xml = (
            '<?xml version="1.0"?>\r\n'
            '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\r\n'
            f'\t<name>{ssid}</name>\r\n'
            f'\t<SSIDConfig><SSID><hex>{hex_ssid}</hex><name>{ssid}</name></SSID></SSIDConfig>\r\n'
            '\t<connectionType>ESS</connectionType>\r\n'
            '\t<connectionMode>manual</connectionMode>\r\n'
            '\t<autoSwitch>false</autoSwitch>\r\n'
            '\t<MSM><security>\r\n'
            '\t\t<authEncryption>\r\n'
            '\t\t\t<authentication>WPA2PSK</authentication>\r\n'
            '\t\t\t<encryption>AES</encryption>\r\n'
            '\t\t\t<useOneX>false</useOneX>\r\n'
            '\t\t</authEncryption>\r\n'
            '\t\t<sharedKey>\r\n'
            '\t\t\t<keyType>passPhrase</keyType>\r\n'
            '\t\t\t<protected>false</protected>\r\n'
            f'\t\t\t<keyMaterial>{password}</keyMaterial>\r\n'
            '\t\t</sharedKey>\r\n'
            '\t</security></MSM>\r\n'
            '</WLANProfile>'
        )
    else:
        xml = (
            '<?xml version="1.0"?>\r\n'
            '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\r\n'
            f'\t<name>{ssid}</name>\r\n'
            f'\t<SSIDConfig><SSID><hex>{hex_ssid}</hex><name>{ssid}</name></SSID></SSIDConfig>\r\n'
            '\t<connectionType>ESS</connectionType>\r\n'
            '\t<connectionMode>manual</connectionMode>\r\n'
            '\t<autoSwitch>false</autoSwitch>\r\n'
            '\t<MSM><security><authEncryption>\r\n'
            '\t\t<authentication>open</authentication>\r\n'
            '\t\t<encryption>none</encryption>\r\n'
            '\t\t<useOneX>false</useOneX>\r\n'
            '\t</authEncryption></security></MSM>\r\n'
            '</WLANProfile>'
        )

    subprocess.run(["cmd", "/c", "mkdir C:\\Temp 2>nul"], capture_output=True)
    Path("C:\\Temp\\glasses.xml").write_bytes(xml.encode("utf-8"))

    _netsh("wlan", "delete", "profile", f"name={ssid}")
    out = _netsh("wlan", "add", "profile", "filename=C:\\Temp\\glasses.xml", "user=current")
    if "error" in out.lower():
        print(f"    프로필 오류: {out[:60]}")
        _netsh("wlan", "connect", f"name={prev}")
        return False

    _netsh("wlan", "connect", f"name={ssid}")

    # 연결 확인: SSID 변경 + 192.168.49.x 대역 OR HTTP 응답
    for _ in range(15):
        time.sleep(1)
        if _current_ssid() == ssid:
            ip_info = _netsh("interface", "ip", "show", "addresses", "Wi-Fi")
            if "192.168.49" in ip_info:
                return True
            # 직접 HTTP 체크
            if _http_ok(GLASSES_IP, timeout=1.0):
                return True
    return False


# ── 메인 ─────────────────────────────────────────────────
async def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    prev_ssid = _current_ssid()

    print("=" * 60)
    print("사진 촬영 → WiFi 다운로드")
    print("=" * 60)

    ble = BLEManager()
    wifi = WiFiTransfer()

    try:
        # ── 1. BLE 연결 ──────────────────────────────────
        print("\n[1] BLE 연결")
        def on_rx(data: bytes):
            print(f"  RX {data.hex()}")
        await ble.connect(DEVICE_ADDR, on_notify=on_rx)
        print(f"  OK")
        await asyncio.sleep(2)

        # ── 2. 사진 촬영 ─────────────────────────────────
        print("\n[2] 사진 촬영")
        await ble.take_photo()
        print("  명령 전송 - 셔터 소리 들리면 다음 진행")
        await asyncio.sleep(3)

        # ── 3. WiFi 활성화 ───────────────────────────────
        print("\n[3] WiFi 활성화")
        await ble.enable_wifi_transfer()
        print("  명령 전송")

        # SSID 감지 대기 (최대 30초)
        print("  WiFi 감지 대기...", end="", flush=True)
        for i in range(30):
            await asyncio.sleep(1)
            if GLASSES_SSID in _scan_ssids():
                print(f" 감지! ({i+1}초)")
                break
            print(".", end="", flush=True)
        else:
            print(" 미감지 - 강제 진행")

        # ── 4. WiFi 연결 ─────────────────────────────────
        print("\n[4] WiFi 연결")
        connected = False
        for pwd in ["", "123456789", "12345678"]:
            label = f"'{pwd}'" if pwd else "'오픈'"
            print(f"  {label}... ", end="", flush=True)
            if _wifi_connect(GLASSES_SSID, pwd, prev_ssid):
                print("성공!")
                connected = True
                break
            print("실패")

        if not connected:
            # HTTP 직접 체크 (이미 연결돼 있을 수도)
            if _http_ok(GLASSES_IP):
                print(f"  HTTP 직접 응답: {GLASSES_IP}")
                connected = True
            else:
                print("\n  WiFi 연결 실패 - 수동 연결 필요:")
                print(f"  SSID: {GLASSES_SSID}")
                print(f"  Password: 123456789 (또는 오픈)")
                input("  연결 후 Enter...")
                if _http_ok(GLASSES_IP):
                    connected = True

        if not connected:
            print("WiFi 연결 불가 - 중단")
            return

        # ── 5. 파일 목록 조회 (패킷 #1: 391B) ───────────
        print(f"\n[5] 파일 목록 조회 ({GLASSES_IP})")
        files = await wifi.fetch_manifest(GLASSES_IP)
        print(f"  {len(files)}개 파일:")
        for f in files:
            print(f"  - {f['filename']} ({f.get('type')})")

        if not files:
            print("  파일 없음")
            return

        # ── 6. 파일 다운로드 (패킷 #2,#3) ───────────────
        print(f"\n[6] 파일 다운로드")
        t_start = time.time()

        async with aiohttp.ClientSession() as session:
            for f in files:
                fname = f['filename']
                ftype = f.get('type', 'misc')
                save_dir = DOWNLOAD_DIR / ftype
                save_dir.mkdir(parents=True, exist_ok=True)

                print(f"  {fname}... ", end="", flush=True)
                try:
                    path = await wifi.download_file(session, GLASSES_IP, fname, str(save_dir))
                    size = Path(path).stat().st_size
                    print(f"{size:,}B")
                except Exception as e:
                    print(f"실패: {e}")

        elapsed = time.time() - t_start
        total = sum(Path(p).stat().st_size for p in DOWNLOAD_DIR.rglob("*.*"))
        print(f"\n  완료: {elapsed:.1f}초 / {total/1024/1024:.1f}MB")
        print(f"  저장: {DOWNLOAD_DIR}")

    finally:
        await ble.disconnect()
        if _current_ssid() != prev_ssid and prev_ssid:
            print(f"\n[복귀] {prev_ssid}...")
            _netsh("wlan", "connect", f"name={prev_ssid}")


if __name__ == "__main__":
    asyncio.run(main())

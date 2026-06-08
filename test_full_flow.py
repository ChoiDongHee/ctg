"""
전체 플로우 테스트: BLE 연결 → 사진 촬영 → WiFi 활성화 → 파일 다운로드
"""
import asyncio
import subprocess
import sys
import io
import time
import socket
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.ble_manager import BLEManager
from core.wifi_transfer import WiFiTransfer

KNOWN_DEVICE  = "3C:A6:DE:5B:EB:50"
GLASSES_SSID  = "WF1_686725cd4952"
PASSWORDS     = ["", "123456789", "12345678", "123456", "88888888"]
CANDIDATE_IPS = ["192.168.49.64", "192.168.49.1", "192.168.49.100", "192.168.43.1"]
DOWNLOAD_DIR  = Path("downloads/full_flow_test")


def _netsh(args: list, decode=True) -> str:
    r = subprocess.run(["netsh"] + args, capture_output=True)
    out = r.stdout.decode("cp949", errors="replace") if r.stdout and decode else ""
    return out.strip()


def _get_current_ssid() -> str:
    out = _netsh(["wlan", "show", "interfaces"])
    for line in out.splitlines():
        if "SSID" in line and "BSSID" not in line:
            return line.split(":", 1)[-1].strip()
    return ""


def _scan_wifi() -> list[str]:
    out = _netsh(["wlan", "show", "networks"])
    ssids = []
    for line in out.splitlines():
        if line.strip().startswith("SSID") and "BSSID" not in line:
            s = line.split(":", 1)[-1].strip()
            if s:
                ssids.append(s)
    return ssids


def _ssid_hex(ssid: str) -> str:
    return ''.join(f'{ord(c):02x}' for c in ssid)

def _make_profile_xml(ssid: str, password: str = "") -> str:
    hex_val = _ssid_hex(ssid)
    if not password:
        return (
            '<?xml version="1.0"?>\r\n'
            '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\r\n'
            f'\t<name>{ssid}</name>\r\n'
            f'\t<SSIDConfig><SSID><hex>{hex_val}</hex><name>{ssid}</name></SSID></SSIDConfig>\r\n'
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
    else:
        return (
            '<?xml version="1.0"?>\r\n'
            '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\r\n'
            f'\t<name>{ssid}</name>\r\n'
            f'\t<SSIDConfig><SSID><hex>{hex_val}</hex><name>{ssid}</name></SSID></SSIDConfig>\r\n'
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


def _check_http(ip: str, timeout=2.0) -> bool:
    try:
        s = socket.create_connection((ip, 80), timeout=timeout)
        s.close()
        return True
    except:
        return False


def _connect_wifi(ssid: str, password: str = "", prev_ssid: str = "") -> bool:
    """WiFi 연결: disconnect → 프로필 추가 → connect → 확인"""
    # 기존 연결 끊기
    _netsh(["wlan", "disconnect"])
    time.sleep(1)

    # XML 저장
    subprocess.run(["cmd", "/c", "mkdir C:\\Temp 2>nul"], capture_output=True)
    xml = _make_profile_xml(ssid, password)
    import codecs
    with codecs.open("C:\\Temp\\glasses.xml", "w", encoding="utf-8-sig") as f:
        f.write(xml)
    # BOM 없이 재저장
    raw = xml.encode("utf-8")
    with open("C:\\Temp\\glasses.xml", "wb") as f:
        f.write(raw)

    # 프로필 삭제 후 재등록
    _netsh(["wlan", "delete", "profile", f"name={ssid}"])
    out = _netsh(["wlan", "add", "profile", "filename=C:\\Temp\\glasses.xml", "user=current"])
    if "error" in out.lower():
        print(f"    [프로필 오류] {out[:80]}")
        # 원래 WiFi 복귀
        if prev_ssid:
            _netsh(["wlan", "connect", f"name={prev_ssid}"])
        return False

    # 연결
    _netsh(["wlan", "connect", f"name={ssid}"])

    # SSID 변경 + 안경 IP 대역 확인으로 판정
    for i in range(12):
        time.sleep(2)
        current = _get_current_ssid()
        if current == ssid:
            # IP 대역 확인 (192.168.49.x 여야 함)
            ip_out = _netsh(["interface", "ip", "show", "addresses", "Wi-Fi"])
            if "192.168.49" in ip_out:
                return True
            # 안경 HTTP 직접 체크
            for ip in CANDIDATE_IPS:
                if _check_http(ip, timeout=1.0):
                    return True
    return False


async def main():
    print("=" * 70)
    print("전체 플로우: BLE → 사진 → WiFi → 다운로드")
    print("=" * 70)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ble  = BLEManager()
    wifi = WiFiTransfer()
    prev_ssid = _get_current_ssid()
    print(f"현재 WiFi: {prev_ssid}")

    try:
        # ── Step 1: BLE 연결 + 알림 로깅 ──────────────────
        print("\n[1] BLE 연결")

        def _on_notify(data: bytes):
            print(f"  [RX] {data.hex()}")

        await ble.connect(KNOWN_DEVICE, on_notify=_on_notify)
        print(f"  OK | 배터리 수신 대기 (15초)...")

        # BC-73 배터리는 10~15초 주기로 자동 전송
        for i in range(15):
            await asyncio.sleep(1)
            if ble.battery_level is not None:
                print(f"  배터리: {ble.battery_level}%")
                break
            print(f"  {i+1}s...", end="\r")
        print()

        # ── Step 2: 사진 촬영 ─────────────────────────────
        print("\n[2] 사진 촬영")
        await ble.take_photo()
        print("  명령 전송")
        await asyncio.sleep(3)

        # ── Step 3: WiFi 활성화 + SSID/PWD 수신 ──────────
        print("\n[3] WiFi 활성화")
        await ble.enable_wifi_transfer()
        print("  명령 전송 | BLE 응답 대기 (40초)...")

        for i in range(40):
            await asyncio.sleep(1)
            if ble._wifi_ssid:
                print(f"\n  [BLE] SSID={ble._wifi_ssid} | PWD={ble._wifi_password}")
                break
            if ble.extracted_ip:
                print(f"\n  [BLE] WiFi IP={ble.extracted_ip}")
                break
            stat = f"배터리:{ble.battery_level}% | SSID:{ble._wifi_ssid} | IP:{ble.extracted_ip}"
            print(f"  {i+1:2d}s | {stat}", end="\r")
        print()

        # BLE에서 비밀번호 받았으면 우선 사용
        ssid = ble._wifi_ssid or GLASSES_SSID
        pwd_list = ([ble._wifi_password] if ble._wifi_password else []) + PASSWORDS

        # ── Step 4: WiFi 스캔 ─────────────────────────────
        print("\n[4] WiFi 스캔")
        networks = _scan_wifi()
        visible = ssid in networks
        print(f"  안경 SSID '{ssid}': {'감지' if visible else '미감지'}")
        if not visible:
            print(f"  감지된 네트워크: {networks[:5]}")

        # ── Step 5: WiFi 연결 ─────────────────────────────
        glasses_ip = ble.extracted_ip or None

        if visible or True:  # 감지 안 돼도 일단 시도
            print("\n[5] WiFi 연결 시도")
            connected = False
            for pwd in pwd_list:
                label = f"'{pwd}'" if pwd else "'오픈'"
                print(f"  {label}... ", end="", flush=True)
                if _connect_wifi(ssid, pwd, prev_ssid):
                    print("성공!")
                    connected = True
                    # IP 탐색
                    for ip in CANDIDATE_IPS:
                        if _check_http(ip):
                            glasses_ip = ip
                            print(f"  HTTP 응답: {ip}")
                            break
                    if glasses_ip:
                        break
                    print("  (연결됐지만 HTTP 미응답 - IP 계속 탐색)")
                    # 실제 할당된 IP 확인
                    ip_out = _netsh(["interface", "ip", "show", "addresses", "Wi-Fi"])
                    for line in ip_out.splitlines():
                        if "IP" in line and ("Address" in line or "주소" in line):
                            print(f"    [PC IP] {line.strip()}")
                    connected = False  # HTTP 안 되면 다음 비밀번호 시도
                else:
                    print("실패")

        # ── Step 6: 파일 다운로드 ─────────────────────────
        if not glasses_ip:
            print(f"\n[6] 파일 다운로드 - 건너뜀 (IP 없음)")
            print(f"  → PC에서 직접 '{ssid}' WiFi 연결 후 재실행")
        else:
            print(f"\n[6] 파일 다운로드 ({glasses_ip})")
            files = await wifi.fetch_manifest(glasses_ip)
            print(f"  파일 목록: {len(files)}개")

            if files:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    for f in files[:5]:
                        fname = f['filename']
                        ftype = f.get('type', 'misc')
                        print(f"  ↓ {fname}... ", end="", flush=True)
                        try:
                            path = await wifi.download_file(
                                session, glasses_ip, fname,
                                str(DOWNLOAD_DIR / ftype)
                            )
                            size = Path(path).stat().st_size
                            print(f"{size:,} bytes")
                        except Exception as e:
                            print(f"실패: {e}")

        # ── 최종 ─────────────────────────────────────────
        print("\n" + "=" * 70)
        print("[결과]")
        print(f"  BLE: {'연결됨' if ble.is_connected else '끊김'}")
        print(f"  배터리: {ble.battery_level}%" if ble.battery_level else "  배터리: 미수신")
        print(f"  미디어: {ble.media_count}")
        print(f"  WiFi IP: {glasses_ip or '없음'}")
        downloaded = list(DOWNLOAD_DIR.rglob("*.*"))
        print(f"  다운로드: {len(downloaded)}개")

    except Exception as e:
        print(f"\n오류: {e}")
        import traceback; traceback.print_exc()
    finally:
        await ble.disconnect()
        # 원래 WiFi 복귀
        current = _get_current_ssid()
        if prev_ssid and current != prev_ssid:
            print(f"\n[복귀] {prev_ssid}...")
            _netsh(["wlan", "connect", f"name={prev_ssid}"])


if __name__ == "__main__":
    asyncio.run(main())

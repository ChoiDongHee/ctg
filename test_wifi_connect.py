"""
안경 WiFi 연결 테스트 - 오픈/WPA2 모두 시도
"""
import subprocess
import time
import sys
import io
import socket

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

GLASSES_SSID = "WF1_686725cd4952"
GLASSES_IP   = "192.168.49.64"
PASSWORDS    = ["", "123456789", "12345678", "123456"]  # 빈 문자열 = 오픈 네트워크

def create_open_profile(ssid: str) -> str:
    return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>open</authentication>
        <encryption>none</encryption>
      </authEncryption>
    </security>
  </MSM>
</WLANProfile>"""

def create_wpa2_profile(ssid: str, password: str) -> str:
    return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>CCMP</encryption>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{password}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>"""

def check_http_reachable(ip: str, port: int = 80, timeout: float = 3.0) -> bool:
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        sock.close()
        return True
    except:
        return False

def get_current_ssid() -> str:
    result = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if "SSID" in line and "BSSID" not in line:
            return line.split(":", 1)[-1].strip()
    return ""

def try_connect(ssid: str, password: str = "") -> bool:
    """WiFi 프로필 생성 후 연결 시도"""
    profile_path = "C:\\Temp\\glasses_test.xml"

    # Temp 폴더 생성
    subprocess.run(["cmd", "/c", "mkdir C:\\Temp 2>nul"], capture_output=True)

    # 프로필 생성
    xml = create_open_profile(ssid) if password == "" else create_wpa2_profile(ssid, password)
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(xml)

    # 기존 프로필 삭제 후 추가
    subprocess.run(["netsh", "wlan", "delete", "profile", f"name={ssid}"], capture_output=True)
    result = subprocess.run(["netsh", "wlan", "add", "profile", f"filename={profile_path}", "user=current"],
                            capture_output=True, text=True)
    if "added" not in result.stdout.lower() and "successfully" not in result.stdout.lower():
        return False

    # 연결
    result = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"],
                            capture_output=True, text=True)

    # 연결 대기
    for _ in range(8):
        time.sleep(1)
        current = get_current_ssid()
        if current == ssid:
            return True
    return False


def main():
    print("=" * 70)
    print("안경 WiFi 연결 테스트")
    print(f"SSID: {GLASSES_SSID}")
    print(f"대상 IP: {GLASSES_IP}")
    print("=" * 70)

    # 현재 WiFi 상태
    current = get_current_ssid()
    print(f"\n[현재 WiFi] {current}")

    # 주변 WiFi 스캔
    print("\n[주변 WiFi 스캔]")
    result = subprocess.run(["netsh", "wlan", "show", "networks"],
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "SSID" in line or "Authentication" in line:
            print(f"  {line.strip()}")

    # 안경 SSID 보이는지 확인
    visible = GLASSES_SSID in result.stdout
    print(f"\n[안경 WiFi 감지] {'YES' if visible else 'NO - 안경 WiFi 활성화 필요'}")

    if not visible:
        print("\n[힌트] BLE로 WiFi 활성화 명령을 보내야 합니다.")
        print("  -> python test_real_device.py 먼저 실행")
        return

    # 비밀번호 순서대로 시도
    print("\n[연결 시도]")
    for pwd in PASSWORDS:
        label = f"'{pwd}'" if pwd else "'(오픈 네트워크)'"
        print(f"\n  비밀번호 {label} 시도...", end=" ", flush=True)

        connected = try_connect(GLASSES_SSID, pwd)

        if connected:
            print("연결 성공!")
            print(f"\n[HTTP 연결 테스트] {GLASSES_IP}:80 ...", end=" ", flush=True)
            if check_http_reachable(GLASSES_IP):
                print("응답!")
                print(f"\n[결론]")
                print(f"  SSID: {GLASSES_SSID}")
                print(f"  Password: {label}")
                print(f"  HTTP 서버: http://{GLASSES_IP}")
                print(f"  -> test_real_device.py 에서 glasses_ip = '{GLASSES_IP}' 사용")
            else:
                print("HTTP 미응답 (IP 다를 수 있음)")
                # IP 스캔
                print(f"\n[IP 탐색] 192.168.49.x 대역...")
                for last in [1, 64, 100, 128, 2, 3]:
                    ip = f"192.168.49.{last}"
                    if check_http_reachable(ip, timeout=1.0):
                        print(f"  {ip}:80 응답!")
            # 원래 WiFi로 복귀
            print(f"\n[복귀] {current} 재연결 중...")
            subprocess.run(["netsh", "wlan", "connect", f"name={current}"], capture_output=True)
            return
        else:
            print("실패")

    print("\n[결론] 모든 비밀번호 시도 실패")
    print("  안경이 WiFi를 방송하고 있는지, 또는 다른 SSID인지 확인 필요")


if __name__ == "__main__":
    main()

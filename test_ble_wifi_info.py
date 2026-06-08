"""
BLE로부터 실제 WiFi 정보 추출
안경이 BC-41 응답에서 SSID/Password 전송
"""
import asyncio
import sys
import io
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.ble_manager import BLEManager

KNOWN_DEVICE = "3C:A6:DE:5B:EB:50"

async def test_ble_wifi_info():
    """BLE로부터 WiFi 정보 추출"""

    print("=" * 80)
    print("BLE WiFi 정보 추출 테스트")
    print("=" * 80)

    ble = BLEManager()

    try:
        # BLE 연결
        print("\n[1] BLE 연결 중...")
        await ble.connect(KNOWN_DEVICE)
        print("✓ BLE 연결 성공")

        # WiFi 활성화
        print("\n[2] WiFi 활성화 명령 전송...")
        await ble.enable_wifi_transfer()
        print("✓ 명령 전송 완료")

        # WiFi 정보 수신 대기 (BC-41 응답)
        print("\n[3] WiFi 정보 수신 대기 (15초)...")
        for i in range(15):
            await asyncio.sleep(1)

            # 실시간 상태 출력
            if ble._wifi_ssid:
                print(f"\n✓ SSID 수신됨!")
                print(f"  SSID: {ble._wifi_ssid}")
                print(f"  Password: {ble._wifi_password if ble._wifi_password else '(미수신)'}")
                break

            if ble.extracted_ip:
                print(f"\n✓ WiFi IP 수신됨!")
                print(f"  IP: {ble.extracted_ip}")

            print(".", end="", flush=True)

        # 최종 상태
        print("\n\n[결과]")
        print("-" * 80)
        print(f"SSID: {ble._wifi_ssid or '(미수신)'}")
        print(f"Password: {ble._wifi_password or '(미수신)'}")
        print(f"WiFi IP: {ble.extracted_ip or '(미수신)'}")
        print(f"Media Count: {ble.media_count}")
        print(f"Battery: {ble.battery_level}%" if ble.battery_level else "Battery: (미수신)")

    except Exception as e:
        print(f"\n✗ 오류: {e}")

    finally:
        print("\n[정리] BLE 연결 해제...")
        await ble.disconnect()
        print("✓ 완료")

if __name__ == "__main__":
    asyncio.run(test_ble_wifi_info())

"""
Wi-Fi 핫스팟 활성화 후 실시간 SSID 스캔
Run: python test_wifi_scan.py
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE  = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def get_wifi_ssids():
    try:
        r = subprocess.run(["netsh", "wlan", "show", "networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except:
        return set()

async def main():
    print("=== Wi-Fi 핫스팟 실시간 탐색 ===\n")

    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("X 못 찾음"); return
    print(f"O {device.name}\n")

    # 현재 Wi-Fi 목록 저장
    before = get_wifi_ssids()
    print(f"현재 Wi-Fi 목록: {before}\n")

    ble_received = []
    def on_notify(sender, data):
        ble_received.append(data)
        txt = data.decode("utf-8", errors="replace")
        print(f"  << BLE: {data.hex()}  |  {txt!r}")

    async with BleakClient(device) as client:
        await asyncio.sleep(1.0)
        await client.start_notify(NOTIFY, on_notify)

        print("[1] Wi-Fi 핫스팟 명령 전송 (0x40)...")
        await client.write_gatt_char(WRITE, pkt(0x40))
        print("  O 전송 완료\n")

        # 30초간 Wi-Fi 목록 실시간 스캔
        print("[2] 30초간 새 SSID 감시 중...")
        found_ssid = None
        for i in range(30):
            await asyncio.sleep(1.0)
            current = get_wifi_ssids()
            new_ssids = current - before
            if new_ssids:
                found_ssid = list(new_ssids)[0]
                print(f"\n  *** 새 SSID 발견: {new_ssids} ***")
                break
            if i % 5 == 4:
                print(f"  {i+1}초... Wi-Fi: {current}")

        if found_ssid:
            print(f"\n안경 SSID: {found_ssid}")
            print("이 SSID에 연결하면 파일 싱크 가능합니다!")
        else:
            print("\n  - 새 SSID 없음")
            print("  → 안경이 핫스팟 안 만든 것 같습니다")
            print("  → 0x40이 아닌 다른 명령일 수 있습니다")

    print("\n=== 완료 ===")

asyncio.run(main())

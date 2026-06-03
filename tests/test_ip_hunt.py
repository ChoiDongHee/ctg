"""
BleIpBridge 방식 - 0x40 후 모든 BLE 채널에서 IP 탐지
Wi-Fi 스캔도 동시 진행
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

# ae01 Write + 모든 Notify 동시 감시
WRITE = "0000ae01-0000-1000-8000-00805f9b34fb"

NOTIFY_CHANNELS = [
    ("ae02", "0000ae02-0000-1000-8000-00805f9b34fb"),
    ("ae04", "0000ae04-0000-1000-8000-00805f9b34fb"),
    ("ae05", "0000ae05-0000-1000-8000-00805f9b34fb"),
    ("UART", "6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
    ("de5b", "de5bf729-d711-4e47-af26-65e3012a5dc7"),
    ("4a02", "00004a02-0000-1000-8000-00805f9b34fb"),
    ("fee3", "0000fee3-0000-1000-8000-00805f9b34fb"),
]

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def get_all_ssids():
    try:
        r = subprocess.run(["netsh", "wlan", "show", "networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except:
        return set()

async def main():
    print("=== BleIpBridge 방식 IP 탐지 ===\n")

    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("X 못 찾음"); return
    print(f"O {device.name} ({device.address})\n")

    before_wifi = get_all_ssids()
    print(f"기존 Wi-Fi: {sorted(before_wifi)}\n")

    found_ip = None
    ble_log = []

    def make_handler(label):
        def h(sender, data):
            # BleIpBridge: 텍스트에서 IP 추출
            try:
                text = data.decode("utf-8", errors="replace")
            except:
                text = ""
            ip = IP_RE.search(text)
            ip_str = ip.group(1) if ip else None

            entry = f"[{label}] {data.hex()} | {repr(text)}"
            if ip_str:
                entry += f"  *** IP={ip_str} ***"
                nonlocal found_ip
                found_ip = ip_str

            ble_log.append(entry)
            print(f"  << {entry}")
        return h

    async with BleakClient(device) as client:
        await asyncio.sleep(1.0)

        # 모든 채널 구독
        for label, uuid in NOTIFY_CHANNELS:
            try:
                await client.start_notify(uuid, make_handler(label))
                print(f"  O {label} 구독됨")
            except Exception as e:
                print(f"  - {label} 구독 실패: {type(e).__name__}")

        print()

        # Wi-Fi 핫스팟 활성화
        print("[1] 0x40 전송 → 25초간 IP 감시...")
        await client.write_gatt_char(WRITE, pkt(0x40))

        new_ssids = set()
        for i in range(25):
            await asyncio.sleep(1.0)
            if found_ip:
                print(f"\n  *** BLE에서 IP 수신: {found_ip} ***")
                break

            # Wi-Fi 변화 감시
            current = get_all_ssids()
            diff = current - before_wifi
            if diff - new_ssids:
                new_ssids |= diff
                print(f"  새 Wi-Fi SSID: {diff}")

            if i % 5 == 4:
                print(f"  {i+1}초... BLE 응답: {len(ble_log)}개")

        if not found_ip and not new_ssids:
            # 배터리도 한번 눌러서 응답 형식 재확인
            print("\n[2] 배터리 조회 (응답 형식 확인)...")
            await client.write_gatt_char(WRITE, pkt(0x20))
            await asyncio.sleep(3.0)

    print(f"\n=== 결과 ===")
    print(f"BLE IP: {found_ip or '없음'}")
    print(f"새 Wi-Fi SSID: {new_ssids or '없음'}")
    print(f"BLE 수신 총 {len(ble_log)}개:")
    for e in ble_log:
        print(f"  {e}")

asyncio.run(main())

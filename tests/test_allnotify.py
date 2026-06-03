"""
모든 Notify 채널 동시 구독 + 실제 데이터 응답 채널 찾기
Run: python test_allnotify.py
"""
import asyncio, sys, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE = "0000ae01-0000-1000-8000-00805f9b34fb"

ALL_NOTIFY = [
    ("ae02", "0000ae02-0000-1000-8000-00805f9b34fb"),
    ("ae04", "0000ae04-0000-1000-8000-00805f9b34fb"),
    ("ae05", "0000ae05-0000-1000-8000-00805f9b34fb"),
    ("4a02", "00004a02-0000-1000-8000-00805f9b34fb"),
    ("fee3", "0000fee3-0000-1000-8000-00805f9b34fb"),
    ("UART", "6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
    ("de5b", "de5bf729-d711-4e47-af26-65e3012a5dc7"),
]

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

async def main():
    print("=== 전체 Notify 채널 + 실제 데이터 탐색 ===\n")

    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("X 못 찾음"); return
    print(f"O {device.name}\n")

    log = []
    def make_handler(label):
        def h(sender, data):
            text = data.decode("utf-8", errors="replace")
            ip = IP_RE.search(text)
            flag = " *** IP!" if ip else ""
            entry = f"[{label}] {data.hex()}  |  {text!r}{flag}"
            log.append(entry)
            print(f"  << {entry}")
        return h

    async with BleakClient(device) as client:
        await asyncio.sleep(1.0)

        # 모든 채널 구독
        for label, uuid in ALL_NOTIFY:
            try:
                await client.start_notify(uuid, make_handler(label))
                print(f"  O {label} 구독")
            except Exception as e:
                print(f"  - {label} 실패: {type(e).__name__}")

        print()

        # 배터리 (실제 데이터 기대)
        print("[1] 배터리 조회 → 5초 대기")
        await client.write_gatt_char(WRITE, pkt(0x20))
        await asyncio.sleep(5.0)

        # 미디어 개수
        print("\n[2] 미디어 개수 → 5초 대기")
        await client.write_gatt_char(WRITE, pkt(0x21))
        await asyncio.sleep(5.0)

        # Wi-Fi 핫스팟 (SSID/PW/IP 기대)
        print("\n[3] Wi-Fi 핫스팟 (0x40) → 15초 대기 (Windows Wi-Fi 목록 확인!)")
        await client.write_gatt_char(WRITE, pkt(0x40))
        await asyncio.sleep(15.0)

        # 사진 촬영
        print("\n[4] 사진 촬영 (0x01)")
        await client.write_gatt_char(WRITE, pkt(0x01))
        await asyncio.sleep(3.0)

    print(f"\n=== 수신된 Notify 총 {len(log)}개 ===")
    for e in log:
        print(f"  {e}")

asyncio.run(main())

"""
ae01/ae02 채널로 Wi-Fi 핫스팟 활성화 + 배터리 테스트
Run: python test_wifi_enable.py
"""
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE  = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

async def main():
    print("=== ae01/ae02 채널 테스트 ===\n")

    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("X 못 찾음")
        return
    print(f"O {device.name}")

    received = []
    def on_notify(sender, data):
        received.append(data)
        try:
            text = data.decode("utf-8", errors="replace")
            print(f"  << [{len(received)}] hex={data.hex()}  text={text!r}")
        except:
            print(f"  << [{len(received)}] hex={data.hex()}")

    async with BleakClient(device) as client:
        await asyncio.sleep(1.0)
        await client.start_notify(NOTIFY, on_notify)
        print("O Notify 구독 완료\n")

        # 배터리 조회
        print("[1] 배터리 조회 (0x20)...")
        received.clear()
        await client.write_gatt_char(WRITE, pkt(0x20))
        await asyncio.sleep(3.0)
        print(f"    응답: {len(received)}개\n")

        # 미디어 개수 조회
        print("[2] 미디어 개수 (0x21)...")
        received.clear()
        await client.write_gatt_char(WRITE, pkt(0x21))
        await asyncio.sleep(3.0)
        print(f"    응답: {len(received)}개\n")

        # Wi-Fi 핫스팟 활성화
        print("[3] Wi-Fi 핫스팟 활성화 (0x40) — 10초 대기...")
        received.clear()
        await client.write_gatt_char(WRITE, pkt(0x40))
        await asyncio.sleep(10.0)
        if received:
            print(f"    O Wi-Fi 응답 수신!")
        else:
            print(f"    - 응답 없음\n")

        # 사진 촬영
        print("[4] 사진 촬영 (0x01)...")
        received.clear()
        await client.write_gatt_char(WRITE, pkt(0x01))
        await asyncio.sleep(3.0)
        print(f"    응답: {len(received)}개")

    print("\n=== 완료 ===")

asyncio.run(main())

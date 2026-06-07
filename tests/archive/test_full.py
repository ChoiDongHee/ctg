"""
M02C 전체 채널 테스트 — 모든 Write/Notify 조합 시도
Run: python test_full.py
"""
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

ALL_NOTIFY = [
    "6e400003-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART TX
    "de5bf729-d711-4e47-af26-65e3012a5dc7",  # de5b service
    "0000ae02-0000-1000-8000-00805f9b34fb",  # ae30 service notify1
    "0000ae04-0000-1000-8000-00805f9b34fb",  # ae30 service notify2
    "00004a02-0000-1000-8000-00805f9b34fb",  # 3802 service
    "0000fee3-0000-1000-8000-00805f9b34fb",  # fee1 service
]

ALL_WRITE = [
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART RX
    "de5bf72a-d711-4e47-af26-65e3012a5dc7",  # de5b service
    "0000ae01-0000-1000-8000-00805f9b34fb",  # ae30 write1
    "0000ae03-0000-1000-8000-00805f9b34fb",  # ae30 write2
]

async def main():
    print("=== M02C 전체 채널 테스트 ===\n")

    # 스캔
    print("[1] 탐색 중...")
    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("  X 못 찾음")
        return
    print(f"  O {device.name} ({device.address})")

    received = {}

    def make_handler(ch_uuid):
        def handler(sender, data):
            received[ch_uuid] = data
            print(f"\n  *** NOTIFY [{ch_uuid[:8]}...]: {data.hex()} ***")
        return handler

    async with BleakClient(device) as client:
        print(f"\n[2] 연결 성공 MTU={client.mtu_size}")
        await asyncio.sleep(1.0)

        # 모든 Notify 구독
        print("\n[3] 모든 Notify 채널 구독...")
        for uuid in ALL_NOTIFY:
            try:
                await client.start_notify(uuid, make_handler(uuid))
                print(f"  O {uuid[:8]}...")
            except Exception as e:
                print(f"  - {uuid[:8]}... 실패: {type(e).__name__}")

        await asyncio.sleep(0.5)

        # 각 Write 채널에 배터리 요청 전송
        print("\n[4] 각 Write 채널에 배터리 요청 (0x20)...")
        for w_uuid in ALL_WRITE:
            received.clear()
            try:
                await client.write_gatt_char(w_uuid, pkt(0x20))
                print(f"  O Write [{w_uuid[:8]}...] 성공")
                await asyncio.sleep(1.5)
                if received:
                    for k, v in received.items():
                        print(f"    -> 응답 [{k[:8]}...]: {v.hex()}")
            except Exception as e:
                print(f"  X Write [{w_uuid[:8]}...] 실패: {type(e).__name__}")

        # Wi-Fi 핫스팟 활성화 (0x40) — Nordic UART로
        print("\n[5] Wi-Fi 핫스팟 활성화 명령 (0x40)...")
        received.clear()
        try:
            await client.write_gatt_char(ALL_WRITE[0], pkt(0x40))
            print("  O 전송 성공 - SSID/PW 응답 대기 (5초)...")
            await asyncio.sleep(5.0)
            if received:
                for k, v in received.items():
                    print(f"  *** Wi-Fi 응답 [{k[:8]}]: {v.hex()}")
                    try:
                        print(f"  *** 텍스트: {v.decode('utf-8', errors='replace')}")
                    except:
                        pass
            else:
                print("  - 응답 없음")
        except Exception as e:
            print(f"  X 실패: {e}")

        # 사진 촬영 (0x01)
        print("\n[6] 사진 촬영 (0x01)...")
        received.clear()
        await client.write_gatt_char(ALL_WRITE[0], pkt(0x01))
        print("  O 전송! 3초 대기...")
        await asyncio.sleep(3.0)
        if received:
            print(f"  O 응답: {list(received.values())[0].hex()}")
        else:
            print("  - 응답 없음 (찰칵 소리 확인)")

    print("\n=== 완료 ===")

asyncio.run(main())

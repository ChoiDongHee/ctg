"""
M02C 자동 탐색 + 사진 촬영 테스트 (비대화형)
Run: python test_photo.py
"""
import asyncio, sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

# 확인된 UUID
WRITE_CHAR  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_ALT   = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY_ALT  = "de5bf729-d711-4e47-af26-65e3012a5dc7"

def pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

async def main():
    print("=== M02C 자동 탐색 + 사진 촬영 ===\n")

    # 1. 스캔
    print("[1] M0* 기기 스캔 중...")
    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device:
        print("  X 못 찾음 - 안경 A1 버튼으로 켜주세요")
        return
    print(f"  O 발견: {device.name} ({device.address})")

    # 2. 연결
    print("[2] 연결 중...")
    received = []

    def on_notify(sender, data):
        received.append(data)
        print(f"  << 응답: {data.hex()}")

    async with BleakClient(device) as client:
        print(f"  O 연결 성공! MTU={client.mtu_size}")
        await asyncio.sleep(1.0)

        # Notify 구독 (Nordic UART TX)
        try:
            await client.start_notify(NOTIFY_CHAR, on_notify)
            print(f"  O Notify 구독: {NOTIFY_CHAR}")
        except Exception as e:
            print(f"  ! Nordic UART TX 실패: {e}")

        # Notify 구독 (보조)
        try:
            await client.start_notify(NOTIFY_ALT, on_notify)
            print(f"  O Notify 구독(alt): {NOTIFY_ALT}")
        except Exception as e:
            print(f"  ! Alt notify 실패: {e}")

        # 3. 배터리 조회 (0x20)
        print("\n[3] 배터리 조회 중...")
        try:
            await client.write_gatt_char(WRITE_CHAR, pkt(0x20))
            await asyncio.sleep(2.0)
            print(f"  응답 수신: {len(received)}개")
        except Exception as e:
            print(f"  X 실패: {e}")

        # 4. 사진 촬영 (0x01)
        print("\n[4] 사진 촬영 명령 전송...")
        received.clear()
        try:
            await client.write_gatt_char(WRITE_CHAR, pkt(0x01))
            print("  O 촬영 명령 전송 성공!")
            await asyncio.sleep(3.0)
            if received:
                print(f"  O 안경 응답: {received[-1].hex()}")
            else:
                print("  - 응답 없음 (찰칵 소리 났나요?)")
        except Exception as e:
            print(f"  X 실패: {e}")
            # alt UUID로 재시도
            try:
                await client.write_gatt_char(WRITE_ALT, pkt(0x01))
                print("  O alt UUID로 촬영 명령 전송 성공!")
                await asyncio.sleep(3.0)
            except Exception as e2:
                print(f"  X alt도 실패: {e2}")

        # 5. 미디어 개수 조회 (0x21)
        print("\n[5] 미디어 개수 조회...")
        received.clear()
        try:
            await client.write_gatt_char(WRITE_CHAR, pkt(0x21))
            await asyncio.sleep(2.0)
            if received:
                print(f"  O 응답: {received[-1].hex()}")
            else:
                print("  - 응답 없음")
        except Exception as e:
            print(f"  X 실패: {e}")

    print("\n=== 완료 ===")

asyncio.run(main())

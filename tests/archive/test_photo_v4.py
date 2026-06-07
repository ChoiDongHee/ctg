"""
사진 촬영만 집중 테스트 — v4 프로토콜
BC 41 03 00 02 01 01 10 50 → Nordic UART 6e400002
"""
import asyncio, sys
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# 응답 받을 수 있는 모든 Notify 채널
NOTIFY_UUIDS = [
    ("UART_TX",  "6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
    ("de5b",     "de5bf729-d711-4e47-af26-65e3012a5dc7"),
    ("ae02",     "0000ae02-0000-1000-8000-00805f9b34fb"),
    ("ae04",     "0000ae04-0000-1000-8000-00805f9b34fb"),
]

PHOTO_PKT   = bytes.fromhex("BC41030002010110 50".replace(" ",""))
COUNT_PKT   = bytes.fromhex("BC41020002040113")


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        crc &= 0xFFFF
    return crc


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def main():
    print(f"[{ts()}] 사진 촬영 테스트 시작")
    print(f"  PHOTO 패킷: {PHOTO_PKT.hex(' ').upper()}")
    print()

    # 스캔
    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev:
        print("X 안경 못 찾음 — A1 버튼으로 켜주세요")
        return
    print(f"[{ts()}] O {dev.name} ({dev.address})")

    # 연결 (재시도)
    client = None
    for i in range(4):
        try:
            c = BleakClient(dev.address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            client = c
            break
        except Exception as e:
            print(f"[{ts()}] 연결 시도 {i+1}/4: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)

    if not client:
        print("X 연결 실패")
        return

    print(f"[{ts()}] O 연결 성공 MTU={client.mtu_size}")

    rx = []
    def handler(label):
        def h(s, d):
            rx.append((label, bytes(d)))
            print(f"[{ts()}] << [{label}] {bytes(d).hex(' ').upper()}")
        return h

    # 모든 채널 구독
    await asyncio.sleep(1.0)
    for label, uuid in NOTIFY_UUIDS:
        try:
            await client.start_notify(uuid, handler(label))
            print(f"[{ts()}] O {label} 구독")
        except Exception as e:
            print(f"[{ts()}] - {label} 실패: {type(e).__name__}")

    print()

    try:
        # ① 촬영 전 앨범 개수
        print(f"[{ts()}] [1] 촬영 전 앨범 개수 조회")
        rx.clear()
        await client.write_gatt_char(WRITE_UUID, COUNT_PKT, response=False)
        await asyncio.sleep(3.0)
        before_rx = list(rx)

        # ② 사진 촬영
        print(f"\n[{ts()}] [2] 사진 촬영: {PHOTO_PKT.hex(' ').upper()}")
        rx.clear()
        await client.write_gatt_char(WRITE_UUID, PHOTO_PKT, response=False)
        print(f"[{ts()}] O 전송 완료 — 5초 대기 (찰칵 소리?)")
        await asyncio.sleep(5.0)
        photo_rx = list(rx)

        # ③ 촬영 후 앨범 개수
        print(f"\n[{ts()}] [3] 촬영 후 앨범 개수 조회")
        rx.clear()
        await client.write_gatt_char(WRITE_UUID, COUNT_PKT, response=False)
        await asyncio.sleep(3.0)
        after_rx = list(rx)

        # 결과 요약
        print(f"\n{'='*50}")
        print(f"전송 전 응답: {len(before_rx)}개  {[r[0]+':'+r[1].hex() for r in before_rx]}")
        print(f"사진 명령 응답: {len(photo_rx)}개  {[r[0]+':'+r[1].hex() for r in photo_rx]}")
        print(f"전송 후 응답: {len(after_rx)}개  {[r[0]+':'+r[1].hex() for r in after_rx]}")

        if photo_rx:
            print("\n✓ Notify 응답 수신! 명령이 인식됨")
        else:
            print("\n? Notify 없음 — 찰칵 소리 났으면 동작한 것")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.disconnect()
        print(f"\n[{ts()}] 종료")

asyncio.run(main())

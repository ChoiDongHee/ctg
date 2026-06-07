"""
de5bf72a 채널 확정 — 사진 촬영 + 앨범 개수 검증
BC [CMD] [LEN] [PAYLOAD] [CRC16] → de5bf72a
응답: BC [CMD] [LEN] [STATUS] [CRC16] ← de5bf729
"""
import asyncio, sys
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"  # ★ 확정
NOTIFY_UUID = "de5bf729-d711-4e47-af26-65e3012a5dc7"  # ★ 확정


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        crc &= 0xFFFF
    return crc


def pkt(cmd, payload):
    p = bytes(payload)
    return bytes([0xBC, cmd]) + len(p).to_bytes(2, "little") + p + crc(p)


def crc(p):
    return crc16(p).to_bytes(2, "little")


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def parse_resp(data):
    if len(data) >= 6 and data[0] == 0xBC:
        cmd  = data[1]
        ln   = int.from_bytes(data[2:4], "little")
        pay  = data[4:4+ln]
        status_code = pay[0] if pay else None
        meaning = {0x00:"SUCCESS", 0x3E:"BUSY/MODE", 0x01:"OK"}.get(status_code, f"0x{status_code:02X}" if status_code is not None else "?")
        return f"CMD=0x{cmd:02X} STATUS={meaning} payload={pay.hex(' ').upper()}"
    return data.hex(" ").upper()


async def main():
    print(f"[{ts()}] de5bf72a 채널 사진 촬영 테스트")
    print()

    devices = await BleakScanner.discover(timeout=10.0)
    dev = next((d for d in devices if (d.name or "").upper().startswith("M0")), None)
    if not dev:
        print("X 못 찾음"); return
    print(f"O {dev.name}")

    client = None
    for i in range(4):
        try:
            c = BleakClient(dev.address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            client = c; break
        except Exception as e:
            print(f"연결 {i+1}/4: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)

    if not client:
        print("X 연결 실패"); return
    print(f"O 연결! MTU={client.mtu_size}\n")

    rx = []
    def h(s, d):
        raw = bytes(d)
        rx.append(raw)
        print(f"[{ts()}] << {parse_resp(raw)}")

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)
    print(f"O Notify 구독: de5bf729\n")

    try:
        # 1. 촬영 전 앨범 개수
        print(f"[{ts()}] [1] 촬영 전 앨범 개수")
        rx.clear()
        await client.write_gatt_char(WRITE_UUID, pkt(0x41, [0x02, 0x04]), response=False)
        await asyncio.sleep(3.0)
        before_count = rx.copy()

        # 2. 사진 촬영
        print(f"\n[{ts()}] [2] 사진 촬영 → BC 41 03 00 02 01 01 CRC")
        rx.clear()
        photo = pkt(0x41, [0x02, 0x01, 0x01])
        print(f"    패킷: {photo.hex(' ').upper()}")
        await client.write_gatt_char(WRITE_UUID, photo, response=False)
        print(f"    O 전송 — 5초 대기 (찰칵?)")
        await asyncio.sleep(5.0)
        photo_resp = rx.copy()

        # 3. 촬영 후 앨범 개수
        print(f"\n[{ts()}] [3] 촬영 후 앨범 개수")
        rx.clear()
        await client.write_gatt_char(WRITE_UUID, pkt(0x41, [0x02, 0x04]), response=False)
        await asyncio.sleep(3.0)
        after_count = rx.copy()

        # 4. 결과
        print(f"\n{'='*50}")
        print(f"촬영 전:  {[r.hex() for r in before_count]}")
        print(f"촬영 응답: {[r.hex() for r in photo_resp]}")
        print(f"촬영 후:  {[r.hex() for r in after_count]}")
        print()

        if before_count != after_count:
            print("✓ 앨범 개수 변화 → 사진 찍힘!")
        elif photo_resp:
            status = photo_resp[0][4] if len(photo_resp[0]) > 4 else "?"
            if status == 0x00:
                print("? 응답 STATUS=SUCCESS 이지만 앨범 변화 없음")
            else:
                print(f"? 응답 STATUS=0x{status:02X} — 다른 CMD 시도 필요")
        else:
            print("? 응답 없음")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()
        print(f"\n[{ts()}] 종료")

asyncio.run(main())

"""
v5 정정 프로토콜 테스트
포맷: BC [CMD] [LEN_LO] [LEN_HI] [CRC_LO] [CRC_HI] [PAYLOAD]
채널: de5bf72a Write / de5bf729 Notify (response=False)
"""
import asyncio, sys
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY_UUID = "de5bf729-d711-4e47-af26-65e3012a5dc7"

WORK_TYPE = {
    0: "대기", 1: "사진모드", 2: "영상녹화",
    3: "영상중지", 4: "전송모드", 5: "OTA",
    6: "사진모드(AI)", 7: "AI대화", 8: "오디오녹음"
}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        crc &= 0xFFFF
    return crc


def build_v5(payload: bytes, cmd: int = 0x41) -> bytes:
    """v5 포맷: BC [CMD] [LEN_LO LEN_HI] [CRC_LO CRC_HI] [PAYLOAD]"""
    length = len(payload).to_bytes(2, "little")
    c = crc16(payload).to_bytes(2, "little")
    return bytes([0xBC, cmd]) + length + c + payload


def parse_v5_response(data: bytes) -> str:
    """v5 응답 파싱: BC [CMD] [LEN] [CRC] [PAYLOAD]"""
    if len(data) < 7 or data[0] != 0xBC:
        return f"raw: {data.hex(' ').upper()}"
    cmd = data[1]
    length = int.from_bytes(data[2:4], "little")
    crc_bytes = data[4:6]
    payload = data[6:6+length] if len(data) >= 6+length else data[6:]

    # CRC 검증
    expected_crc = crc16(payload).to_bytes(2, "little")
    crc_ok = "✓" if crc_bytes == expected_crc else "✗"

    mode = payload[0] if payload else None
    mode_str = WORK_TYPE.get(mode, f"0x{mode:02X}") if mode is not None else "?"
    return f"CMD=0x{cmd:02X} mode={mode_str} payload={payload.hex(' ').upper()} CRC={crc_ok}"


def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def try_connect(address, n=4):
    for i in range(n):
        try:
            c = BleakClient(address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            return c
        except Exception as e:
            print(f"[{ts()}] 연결 {i+1}/{n}: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)
    raise RuntimeError("연결 실패")


async def main():
    print(f"[{ts()}] v5 정정 패킷 테스트")
    print(f"  포맷: BC [CMD] [LEN] [CRC] [PAYLOAD]")
    print()

    devices = await BleakScanner.discover(timeout=10.0)
    dev = next((d for d in devices if (d.name or "").upper().startswith("M0")), None)
    if not dev:
        print("X 못 찾음"); return
    print(f"O {dev.name}\n")

    client = await try_connect(dev.address)
    print(f"O 연결! MTU={client.mtu_size}\n")

    rx = []
    def h(s, d):
        raw = bytes(d)
        rx.append(raw)
        print(f"[{ts()}] << {parse_v5_response(raw)}")

    await asyncio.sleep(1.0)
    await client.start_notify(NOTIFY_UUID, h)
    print(f"O Notify: de5bf729\n")

    try:
        cmds = [
            ("앨범 개수",  build_v5(bytes([0x02, 0x04]))),
            ("사진 촬영",  build_v5(bytes([0x02, 0x01, 0x01]))),
            ("앨범 개수",  build_v5(bytes([0x02, 0x04]))),
        ]

        for desc, pkt in cmds:
            print(f"[{ts()}] --- {desc} ---")
            print(f"  WRITE: {pkt.hex(' ').upper()}")
            rx.clear()
            await client.write_gatt_char(WRITE_UUID, pkt, response=False)
            print(f"  O 전송" + (" — 5초 (찰칵?)" if "사진" in desc else " — 3초"))
            await asyncio.sleep(5.0 if "사진" in desc else 3.0)
            print(f"  응답: {len(rx)}개")
            print()

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()
        print(f"[{ts()}] 종료")

asyncio.run(main())

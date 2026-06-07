"""
사진 촬영 — 모든 채널 + 모든 패킷 형식 브루트포스 테스트
"""
import asyncio, sys
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

UART_W  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
UART_N  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DE5B_W  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
DE5B_N  = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE01_W  = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_N  = "0000ae02-0000-1000-8000-00805f9b34fb"


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        crc &= 0xFFFF
    return crc


def bc_pkt(cmd, payload):
    length = len(payload).to_bytes(2, "little")
    c = crc16(payload).to_bytes(2, "little")
    return bytes([0xBC, cmd]) + length + payload + c


PAYLOAD = bytes([0x02, 0x01, 0x01])  # 사진

# 시도할 패킷 형식들
PACKETS = [
    ("v4 CMD=0x41", UART_W,  bc_pkt(0x41, PAYLOAD)),
    ("v4 CMD=0x73", UART_W,  bc_pkt(0x73, PAYLOAD)),
    ("v4 CMD=0x02", UART_W,  bc_pkt(0x02, PAYLOAD)),
    ("raw payload", UART_W,  PAYLOAD),
    ("v4 CMD=0x41", DE5B_W,  bc_pkt(0x41, PAYLOAD)),
    ("v4 CMD=0x73", DE5B_W,  bc_pkt(0x73, PAYLOAD)),
    ("raw payload", DE5B_W,  PAYLOAD),
    ("aa55 cmd=01", AE01_W,  bytes([0xAA,0x55,0x01,0x00,0x01])),
]

def ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def main():
    print(f"[{ts()}] 사진 촬영 브루트포스 테스트")
    print("사진 찍히면 즉시 알려주세요!\n")

    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev:
        print("X 못 찾음"); return
    print(f"O {dev.name}\n")

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
    def h(label):
        def handler(s, d):
            rx.append((label, bytes(d)))
            print(f"  << [{label}] {bytes(d).hex(' ').upper()}")
        return handler

    await asyncio.sleep(1.0)
    for label, uuid in [("UART_TX",UART_N),("de5b",DE5B_N),("ae02",AE02_N)]:
        try: await client.start_notify(uuid, h(label)); print(f"O {label}")
        except: pass
    print()

    try:
        for desc, write_uuid, pkt in PACKETS:
            ch = "UART" if write_uuid == UART_W else ("de5b" if write_uuid == DE5B_W else "ae01")
            print(f"[{ts()}] [{ch}] {desc}: {pkt.hex(' ').upper()}")
            rx.clear()
            try:
                await client.write_gatt_char(write_uuid, pkt, response=False)
                print(f"  O 전송 — 3초 대기 (찰칵?)")
            except Exception as e:
                print(f"  X {e}")
                continue
            await asyncio.sleep(3.0)
            if rx:
                print(f"  !! 응답: {[(l,d.hex()) for l,d in rx]}")
            print()

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected:
            await client.disconnect()
        print("종료")

asyncio.run(main())

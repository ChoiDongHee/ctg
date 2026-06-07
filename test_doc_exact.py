"""
HeyCyan_MO2E_CLI_개발_분석_v4-1.md 섹션 9 코드 그대로 실행
수정 없음
"""
import asyncio, sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakClient, BleakScanner

SERVICE_UUID = "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        crc &= 0xFFFF
    return crc


def build_packet(payload: bytes, cmd: int = 0x41) -> bytes:
    return (
        bytes([0xBC, cmd])
        + len(payload).to_bytes(2, "little")
        + payload
        + crc16_modbus(payload).to_bytes(2, "little")
    )


def on_notify(sender, data: bytearray):
    print("[NOTIFY]", data.hex(" ").upper())

    with open("notify_log.txt", "a", encoding="utf-8") as f:
        f.write(data.hex(" ").upper() + "\n")


async def find_device():
    devices = await BleakScanner.discover(timeout=10)

    print("[SCAN] devices:")
    for d in devices:
        print(f"  {d.address}  {d.name}")

    # M02C 우선 (확정된 안경 이름 패턴)
    for d in devices:
        name = d.name or ""
        if name.upper().startswith("M0"):
            print(f"[MATCH] M0* 기기: {d.name}")
            return d

    # 보조 필터
    for d in devices:
        name = d.name or ""
        if "Cyan" in name or "Hey" in name or "MO2" in name:
            print(f"[MATCH] 보조 필터: {d.name}")
            return d

    return None


async def main():
    target = await find_device()

    if target is None:
        print("[ERROR] target device not found")
        return

    print(f"[CONNECT] {target.address} {target.name}")

    async with BleakClient(target.address) as client:
        print("[CONNECTED]", client.is_connected)

        await client.start_notify(NOTIFY_UUID, on_notify)
        print("[NOTIFY] subscribed")

        photo_payload = bytes.fromhex("02 01 01".replace(" ", ""))
        photo_packet = build_packet(photo_payload)

        print("[WRITE]", photo_packet.hex(" ").upper())

        # 일부 BLE 기기는 response=False가 더 잘 동작할 수 있음.
        # 먼저 response=False로 테스트하고, 실패하면 True로 변경.
        await client.write_gatt_char(WRITE_UUID, photo_packet, response=False)

        await asyncio.sleep(5)

        await client.stop_notify(NOTIFY_UUID)
        print("[DONE]")


if __name__ == "__main__":
    asyncio.run(main())

"""
HeyCyan M02C connect + UUID test
Run: python test_connect.py
"""
import asyncio, sys, struct, time

# Windows 터미널 UTF-8 강제
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from bleak import BleakScanner, BleakClient

TARGET_ADDRESS = None  # None이면 M0* 이름으로 자동 탐색

def build_packet(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([cmd, len(payload)]) + payload
    checksum = sum(body) & 0xFF
    return bytes([0xAA, 0x55]) + body + bytes([checksum])

def is_vendor_uuid(uuid: str) -> bool:
    return not uuid.lower().startswith("0000")

async def main():
    print("=" * 60)
    print(f"HeyCyan M02C 연결 테스트: {TARGET_ADDRESS}")
    print("=" * 60)

    # 1. 스캔
    print("\n[1] 기기 탐색 중...")
    if TARGET_ADDRESS:
        device = await BleakScanner.find_device_by_address(TARGET_ADDRESS, timeout=12.0)
    else:
        # M0* 이름으로 자동 탐색
        all_devices = await BleakScanner.discover(timeout=12.0)
        device = next(
            (d for d in all_devices if (d.name or "").lower().startswith("m0")),
            None
        )
    if not device:
        print("  X 기기 못 찾음 - 안경 켜져 있는지 확인")
        return
    print(f"  O 발견: {device.name} / {device.address}")

    # 2. 연결 + 서비스 목록 출력
    print("\n[2] 연결 중...")
    async with BleakClient(device) as client:
        print(f"  O 연결 성공! MTU={client.mtu_size}")
        await asyncio.sleep(1.0)

        print("\n[3] 서비스/특성 전체 목록:")
        write_chars = []
        notify_chars = []

        for svc in client.services:
            vendor = "(*)" if is_vendor_uuid(str(svc.uuid)) else "   "
            print(f"\n  {vendor} SVC: {svc.uuid}")
            for ch in svc.characteristics:
                props = list(ch.properties)
                v = "(*)" if is_vendor_uuid(str(ch.uuid)) else "   "
                print(f"       {v} CHAR: {ch.uuid}  [{', '.join(props)}]")

                if is_vendor_uuid(str(ch.uuid)):
                    if "write" in props or "write-without-response" in props:
                        write_chars.append((str(svc.uuid), str(ch.uuid), props))
                    if "notify" in props:
                        notify_chars.append((str(svc.uuid), str(ch.uuid), props))

        print(f"\n[4] 벤더 Write 특성: {len(write_chars)}개")
        for svc_u, ch_u, props in write_chars:
            print(f"    {ch_u}  [{', '.join(props)}]")

        print(f"\n[5] 벤더 Notify 특성: {len(notify_chars)}개")
        for svc_u, ch_u, props in notify_chars:
            print(f"    {ch_u}  [{', '.join(props)}]")

        # 3. Notify 구독
        if notify_chars:
            notif_uuid = notify_chars[0][1]
            received = []

            def on_notify(sender, data):
                received.append(data)
                print(f"  << NOTIFY: {data.hex()}")

            print(f"\n[6] Notify 구독: {notif_uuid}")
            await client.start_notify(notif_uuid, on_notify)
            await asyncio.sleep(0.5)

        # 4. Write 테스트 - 배터리 요청 (0x20)
        if write_chars:
            write_uuid = write_chars[0][1]
            pkt = build_packet(0x20)  # 배터리 조회
            print(f"\n[7] Write 테스트 - 배터리 요청 (0x20)")
            print(f"    UUID: {write_uuid}")
            print(f"    데이터: {pkt.hex()}")
            try:
                await client.write_gatt_char(write_uuid, pkt)
                print("    O Write 성공!")
                await asyncio.sleep(2.0)
                if received:
                    print(f"    O 응답 수신: {received[-1].hex()}")
                else:
                    print("    - 응답 없음 (정상일 수 있음)")
            except Exception as e:
                print(f"    X Write 실패: {e}")

            # 사진 촬영 테스트
            yn = input("\n[8] 사진 촬영 테스트? (y/N): ").strip().lower()
            if yn == "y":
                pkt2 = build_packet(0x01)
                try:
                    await client.write_gatt_char(write_uuid, pkt2)
                    print("  O 촬영 명령 전송!")
                    await asyncio.sleep(2.0)
                except Exception as e:
                    print(f"  X 실패: {e}")
        else:
            print("\n[!] 벤더 Write 특성 없음 - UUID 확인 필요")

    print("\n완료")

if __name__ == "__main__":
    asyncio.run(main())

"""
실제 프로토콜 테스트: [0x02, cmd, param] → de5bf72a
타임아웃 60초, 3회 재시도
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE01   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02   = "0000ae02-0000-1000-8000-00805f9b34fb"

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

async def try_connect(address: str, max_attempts: int = 5) -> BleakClient:
    """최대 5회, 회당 60초 타임아웃으로 연결 시도"""
    for attempt in range(max_attempts):
        print(f"  연결 시도 {attempt+1}/{max_attempts}...")
        client = BleakClient(address, timeout=60.0)
        try:
            await asyncio.wait_for(client.connect(timeout=60.0), timeout=65.0)
            print(f"  O 연결 성공!")
            return client
        except Exception as e:
            print(f"  X 실패: {type(e).__name__}: {str(e)[:60]}")
            try: await client.disconnect()
            except: pass
            if attempt < max_attempts - 1:
                wait = 5 + attempt * 3
                print(f"  {wait}초 대기 후 재시도...")
                await asyncio.sleep(wait)
    raise RuntimeError("연결 실패 (5회)")

async def main():
    print("=" * 60)
    print("실제 프로토콜 테스트: [0x02, cmd, param]")
    print("=" * 60)

    # 스캔
    print("\n[스캔] M0* 기기 탐색...")
    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev: print("X 못 찾음 — 안경 켜주세요"); return
    print(f"O {dev.name} ({dev.address})\n")

    rx = []
    found_ip = [None]
    before_wifi = get_ssids()

    def h(label):
        def handler(s, data):
            txt = data.decode("utf-8", errors="replace")
            ip = IP_RE.search(txt)
            if ip: found_ip[0] = ip.group(1)
            rx.append((label, data))
            flag = f"  *** IP={ip.group(1)}!" if ip else ""
            print(f"  << [{label}] {data.hex()} | {repr(txt[:60])}{flag}")
        return handler

    client = await try_connect(dev.address)

    try:
        await asyncio.sleep(1.0)

        # Notify 구독
        try:
            await client.start_notify(NOTIFY, h("de5b"))
            print("O de5b Notify 구독")
        except Exception as e:
            print(f"X de5b Notify 실패: {e}")
        try:
            await client.start_notify(AE02, h("ae02"))
            print("O ae02 Notify 구독")
        except: pass
        print()

        # 1. 사진 촬영
        print("[1] 사진 촬영: bytes([0x02, 0x01, 0x01])")
        rx.clear()
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x01]))
        print("  O 전송 완료 — 3초 대기 (찰칵 소리?)")
        await asyncio.sleep(3.0)
        print(f"  응답: {len(rx)}개\n")

        # 2. 미디어 개수
        print("[2] 미디어 개수: bytes([0x02, 0x04])")
        rx.clear()
        await client.write_gatt_char(WRITE, bytes([0x02, 0x04]))
        await asyncio.sleep(3.0)
        print(f"  응답: {len(rx)}개\n")

        # 3. Wi-Fi 전송 모드
        print("[3] Wi-Fi 전송 모드: bytes([0x02, 0x01, 0x04]) — 20초 대기")
        print("    (Wi-Fi 핫스팟 생성, SSID/비번=123456789)")
        rx.clear()
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x04]))
        for i in range(20):
            await asyncio.sleep(1.0)
            if found_ip[0]:
                print(f"\n  *** BLE IP 수신: {found_ip[0]} ***")
                break
            new = get_ssids() - before_wifi
            if new:
                print(f"\n  *** 새 Wi-Fi SSID: {new} ***")
                before_wifi |= new
            if (i+1) % 5 == 0:
                print(f"  {i+1}초... 응답: {len(rx)}개")

        print(f"\n  최종: IP={found_ip[0]}, SSID변화={get_ssids()-before_wifi}\n")

        # 4. 영상
        print("[4] 영상 녹화 시작: bytes([0x02, 0x01, 0x02])")
        rx.clear()
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x02]))
        await asyncio.sleep(3.0)
        print(f"  응답: {len(rx)}개")

        print("[5] 영상 녹화 중지: bytes([0x02, 0x01, 0x03])")
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x03]))
        await asyncio.sleep(2.0)

        print("\n[6] 오디오 녹음 시작: bytes([0x02, 0x01, 0x08])")
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x08]))
        await asyncio.sleep(3.0)
        print("[7] 오디오 녹음 중지: bytes([0x02, 0x01, 0x0C])")
        await client.write_gatt_char(WRITE, bytes([0x02, 0x01, 0x0C]))
        await asyncio.sleep(2.0)

    except Exception as e:
        print(f"\n오류: {e}")
    finally:
        if client.is_connected:
            await client.disconnect()

    print("\n=== 전체 수신 로그 ===")
    for label, d in rx:
        print(f"  [{label}] {d.hex()}")

asyncio.run(main())

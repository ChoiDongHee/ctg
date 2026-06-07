"""
페어링(BONDED) 후 명령 전송 테스트
nRF Connect 로그 분석: type=03 응답은 Bonded 연결에서만 옴
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from bleak import BleakScanner, BleakClient

DE5B_W = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
DE5B_N = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE01   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02   = "0000ae02-0000-1000-8000-00805f9b34fb"
UART_W = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
UART_N = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

def aa55(cmd, *payload):
    body = bytes([cmd, len(payload)]) + bytes(payload)
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

async def main():
    print("=" * 60)
    print("BONDED 연결 후 명령 테스트")
    print("=" * 60)

    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"\nO {dev.name} ({dev.address})\n")

    rx = []
    found_ip = [None]
    before = get_ssids()

    def h(label):
        def handler(s, data):
            txt = data.decode("utf-8", errors="replace")
            m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', txt)
            if m: found_ip[0] = m.group(1)
            rx.append((label, data))
            # 배터리 파싱 (type=03)
            if len(data) >= 9 and data[0] == 0xBC and data[1] == 0x73 and data[2] == 0x03:
                for i in range(4, len(data)-2):
                    if data[i] == 0x05:
                        batt = data[i+1]
                        charging = data[i+2]
                        print(f"  << [{label}] {data.hex()} | 배터리={batt}% 충전={charging}")
                        return
            print(f"  << [{label}] {data.hex()}" + (" *** IP!" if m else ""))
        return handler

    client = BleakClient(dev.address, timeout=60.0)
    try:
        print("[1] 연결 중...")
        await asyncio.wait_for(client.connect(timeout=60.0), timeout=65.0)
        print(f"O 연결 성공! MTU={client.mtu_size}")

        # 페어링 시도
        print("\n[2] 페어링(BOND) 시도...")
        try:
            paired = await client.pair()
            print(f"O 페어링 {'성공' if paired else '이미 페어링됨'}")
        except Exception as e:
            print(f"  페어링: {type(e).__name__} - {e}")

        await asyncio.sleep(2.0)

        # 모든 Notify 구독 (nRF Connect와 동일하게)
        print("\n[3] Notify 구독 (nRF Connect 방식)...")
        for uuid, label in [
            (AE02,   "ae02"),
            ("0000ae04-0000-1000-8000-00805f9b34fb", "ae04"),
            ("0000ae05-0000-1000-8000-00805f9b34fb", "ae05"),
            ("00004a02-0000-1000-8000-00805f9b34fb", "4a02"),
            ("0000ae3c-0000-1000-8000-00805f9b34fb", "ae3c"),
            (UART_N, "UART_TX"),
            (DE5B_N, "de5b"),
            ("0000fee3-0000-1000-8000-00805f9b34fb", "fee3"),
        ]:
            try:
                await client.start_notify(uuid, h(label))
                print(f"  O {label}")
            except Exception as e:
                print(f"  - {label}: {type(e).__name__}")

        # type=03 응답 대기 (10초마다 옴)
        print("\n[4] type=03 응답 대기 (15초)...")
        for i in range(15):
            await asyncio.sleep(1.0)
            type03 = [(l,d) for l,d in rx if len(d)>2 and d[0]==0xBC and d[1]==0x73 and d[2]==0x03]
            if type03:
                print(f"  O type=03 수신! {len(type03)}개")
                break
            if (i+1)%5==0: print(f"  {i+1}초... type=03: 0개")

        # 사진 촬영 시도
        print("\n[5] 사진 촬영 명령들 시도...")
        for name, uuid, data in [
            ("ae01 aa55[01]",    AE01,   aa55(0x01)),
            ("ae01 aa55[02]",    AE01,   aa55(0x02)),
            ("de5b [020101]",    DE5B_W, bytes([0x02, 0x01, 0x01])),
            ("UART [020101]",    UART_W, bytes([0x02, 0x01, 0x01])),
        ]:
            rx.clear()
            try:
                await client.write_gatt_char(uuid, data)
                print(f"  O [{name}] {data.hex()}")
                await asyncio.sleep(3.0)
                t3 = [(l,d) for l,d in rx if len(d)>2 and d[0]==0xBC and d[1]==0x73 and d[2]==0x03]
                if t3:
                    print(f"    O type=03 응답!")
                else:
                    print(f"    - 응답: {len(rx)}개 (type=03 없음)")
            except Exception as e:
                print(f"  X [{name}] {e}")

        # Wi-Fi 활성화
        print("\n[6] Wi-Fi 활성화 시도...")
        rx.clear()
        try:
            await client.write_gatt_char(AE01, aa55(0x40))
            print("  O ae01 aa55[0x40] 전송")
        except: pass
        for i in range(15):
            await asyncio.sleep(1.0)
            new = get_ssids() - before
            if new: print(f"  *** 새 SSID: {new}"); break
            if found_ip[0]: print(f"  *** IP: {found_ip[0]}"); break
            if (i+1)%5==0: print(f"  {i+1}초...")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected: await client.disconnect()

    print(f"\n새 SSID: {get_ssids()-before}")
    print(f"IP: {found_ip[0]}")

asyncio.run(main())

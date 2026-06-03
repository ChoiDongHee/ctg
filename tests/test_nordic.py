"""
Nordic UART (6e400002 Write / 6e400003 Notify) 테스트
LargeDataHandler -> UUID_WRITE = 6e400002 가설
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from bleak import BleakScanner, BleakClient

UART_W = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART RX (Write)
UART_N = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART TX (Notify)
AE01   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02   = "0000ae02-0000-1000-8000-00805f9b34fb"
DE5B_N = "de5bf729-d711-4e47-af26-65e3012a5dc7"

def aa55(cmd, *payload):
    body = bytes([cmd, len(payload)]) + bytes(payload)
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

async def try_connect(addr, n=4):
    for i in range(n):
        try:
            c = BleakClient(addr, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            return c
        except Exception as e:
            print(f"  [{i+1}/{n}] {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)
    raise RuntimeError("연결 실패")

async def main():
    print("=" * 60)
    print("Nordic UART (6e400002/3) + 전채널 구독 테스트")
    print("=" * 60)

    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"\nO {dev.name}\n")

    before = get_ssids()
    rx = []
    found_ip = [None]

    def h(label):
        def handler(s, data):
            txt = data.decode("utf-8", errors="replace")
            m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', txt)
            if m: found_ip[0] = m.group(1)
            rx.append((label, data))
            print(f"  << [{label}] {data.hex()} | {repr(txt[:80])}" + (" *** IP!" if m else ""))
        return handler

    client = await try_connect(dev.address)
    try:
        await asyncio.sleep(1.0)
        for uuid, label in [
            (UART_N, "UART_TX"),
            (AE02,   "ae02"),
            (DE5B_N, "de5b"),
        ]:
            try: await client.start_notify(uuid, h(label)); print(f"O {label}")
            except Exception as e: print(f"X {label}: {e}")
        print()

        tests = [
            # Nordic UART 직접 write
            ("UART 배터리 0x20",      UART_W, aa55(0x20)),
            ("UART 사진 raw[020101]",  UART_W, bytes([0x02, 0x01, 0x01])),
            ("UART 사진 aa55[01]",     UART_W, aa55(0x01)),
            ("UART 사진 aa55[02]",     UART_W, aa55(0x02)),
            ("UART 미디어 raw[0204]",  UART_W, bytes([0x02, 0x04])),
            ("UART Wi-Fi raw[020104]", UART_W, bytes([0x02, 0x01, 0x04])),
        ]

        for name, write_uuid, data in tests:
            print(f"[{name}]  {data.hex()}")
            rx.clear()
            try:
                await client.write_gatt_char(write_uuid, data)
                print(f"  O 전송")
            except Exception as e:
                print(f"  X {e}")
                continue
            await asyncio.sleep(3.0)
            print(f"  응답: {len(rx)}개\n")

        # Wi-Fi + SSID 감시
        print("[Wi-Fi 최종: UART raw [0x02,0x01,0x04]] — 20초")
        rx.clear()
        await client.write_gatt_char(UART_W, bytes([0x02, 0x01, 0x04]))
        for i in range(20):
            await asyncio.sleep(1.0)
            new = get_ssids() - before
            if new: print(f"  *** 새 SSID: {new}")
            if found_ip[0]: print(f"  *** IP: {found_ip[0]}"); break
            if (i+1)%5==0: print(f"  {i+1}초 응답:{len(rx)}개")

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected: await client.disconnect()

    print(f"\n새 SSID: {get_ssids()-before}")
    print(f"IP: {found_ip[0]}")

asyncio.run(main())

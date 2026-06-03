"""
CMD_TAKING_PICTURE = 0x02 테스트
aa55 format to ae01, subscribe to BOTH ae02 AND de5b
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from bleak import BleakScanner, BleakClient

AE01   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02   = "0000ae02-0000-1000-8000-00805f9b34fb"
DE5B_W = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
DE5B_N = "de5bf729-d711-4e47-af26-65e3012a5dc7"

def aa55(cmd, *payload):
    body = bytes([cmd, len(payload)]) + bytes(payload)
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def bc73(payload: bytes) -> bytes:
    full = bytearray([0xBC, 0x73] + list(payload) + [0x00])
    full[-1] = sum(full[:-1]) & 0xFF
    return bytes(full)

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

async def send_test(client, name, write_uuid, data, rx, wait=3.0):
    rx.clear()
    try:
        await client.write_gatt_char(write_uuid, data)
        print(f"  O [{name}] 전송: {data.hex()}")
    except Exception as e:
        print(f"  X [{name}] 실패: {e}")
        return
    await asyncio.sleep(wait)
    print(f"  응답: {len(rx)}개")
    print()

async def main():
    print("=" * 60)
    print("CMD_TAKING_PICTURE=0x02 + aa55/ae01 + de5b 동시 감시")
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
            print(f"  << [{label}] {data.hex()} | {repr(txt[:60])}" + (f" *** IP!" if m else ""))
        return handler

    client = await try_connect(dev.address)
    try:
        await asyncio.sleep(1.0)
        for uuid, label in [(DE5B_N,"de5b"),(AE02,"ae02")]:
            try: await client.start_notify(uuid, h(label)); print(f"O {label}")
            except: pass
        print()

        # CMD_TAKING_PICTURE = 0x02 (from Constants.class constant pool)
        tests = [
            ("배터리 aa55 cmd=0x20",  AE01, aa55(0x20)),
            ("사진 aa55 cmd=0x01",    AE01, aa55(0x01)),
            ("사진 aa55 cmd=0x02",    AE01, aa55(0x02)),         # CMD_TAKING_PICTURE
            ("사진 aa55 cmd=0x02,01", AE01, aa55(0x02, 0x01)),   # with payload
            ("사진 bc73 0201",        DE5B_W, bc73(bytes([0x02, 0x01, 0x01]))),
            ("미디어수 aa55 0x21",    AE01, aa55(0x21)),
        ]

        for name, uuid, data in tests:
            print(f"[{name}]")
            await send_test(client, name, uuid, data, rx)

        # Wi-Fi 테스트
        print("[Wi-Fi aa55 cmd=0x40]")
        rx.clear()
        await client.write_gatt_char(AE01, aa55(0x40))
        print(f"  O 전송 — 20초 대기...")
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

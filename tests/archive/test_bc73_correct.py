"""
bc73 올바른 프레임 형식 테스트
format: [0xBC, 0x73, payload..., CRC]
CRC = sum(all_bytes_except_last) & 0xFF
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE02   = "0000ae02-0000-1000-8000-00805f9b34fb"

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

def bc73(payload: bytes) -> bytes:
    """bc73 프레임: [BC 73] + payload + [CRC]"""
    data = bytes([0xBC, 0x73]) + payload + bytes([0x00])  # CRC placeholder
    full = bytearray(data)
    full[-1] = sum(full[:-1]) & 0xFF
    return bytes(full)

async def try_connect(address, attempts=4):
    for i in range(attempts):
        try:
            c = BleakClient(address, timeout=60.0)
            await asyncio.wait_for(c.connect(timeout=60.0), timeout=65.0)
            return c
        except Exception as e:
            print(f"  시도 {i+1}/{attempts}: {type(e).__name__}")
            try: await c.disconnect()
            except: pass
            await asyncio.sleep(5)
    raise RuntimeError("연결 실패")

async def main():
    print("=" * 60)
    print("bc73 올바른 프레임 형식 테스트")
    print("=" * 60)

    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev: print("X 못 찾음"); return
    print(f"\nO {dev.name}\n")

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
        await client.start_notify(NOTIFY, h("de5b"))
        try: await client.start_notify(AE02, h("ae02"))
        except: pass
        print("O Notify 구독\n")

        cmds = [
            ("사진 촬영",      bc73(bytes([0x02, 0x01, 0x01]))),
            ("미디어 개수",     bc73(bytes([0x02, 0x04]))),
            ("Wi-Fi 전송모드", bc73(bytes([0x02, 0x01, 0x04]))),
            ("영상 시작",      bc73(bytes([0x02, 0x01, 0x02]))),
            ("영상 중지",      bc73(bytes([0x02, 0x01, 0x03]))),
        ]

        for name, pkt in cmds:
            print(f"[{name}] {pkt.hex()}")
            rx.clear()
            try:
                await client.write_gatt_char(WRITE, pkt)
                print(f"  O 전송 완료")
            except Exception as e:
                print(f"  X 실패: {e}")
                continue

            if "Wi-Fi" in name:
                print("  20초 대기...")
                for i in range(20):
                    await asyncio.sleep(1.0)
                    new = get_ssids() - before_wifi
                    if new: print(f"  *** 새 SSID: {new} ***")
                    if found_ip[0]: print(f"  *** IP: {found_ip[0]} ***"); break
                    if (i+1)%5==0: print(f"  {i+1}초 응답: {len(rx)}개")
            else:
                await asyncio.sleep(3.0)
                print(f"  응답: {len(rx)}개")
            print()

    except Exception as e:
        print(f"오류: {e}")
    finally:
        if client.is_connected: await client.disconnect()

    print(f"IP={found_ip[0]}")
    print(f"새 Wi-Fi={get_ssids()-before_wifi}")

asyncio.run(main())

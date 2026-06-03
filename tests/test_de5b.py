"""
de5b 채널 프로토콜 분석 + Wi-Fi 명령 시도
bc73 헤더 형식으로 Wi-Fi 활성화 시도
"""
import asyncio, sys, subprocess, re
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

# de5b 채널
DE5B_WRITE  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
DE5B_NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE01_WRITE  = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"
UART_WRITE  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def pkt_aa55(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

async def main():
    print("=== de5b 채널 + Wi-Fi 탐색 ===\n")

    devices = await BleakScanner.discover(timeout=12.0)
    device = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not device: print("X 못 찾음"); return
    print(f"O {device.name}\n")

    before = get_ssids()
    all_rx = []
    found_ip = [None]

    def handler(label):
        def h(s, data):
            txt = data.decode("utf-8", errors="replace")
            ip = IP_RE.search(txt)
            if ip: found_ip[0] = ip.group(1)
            all_rx.append((label, data))
            flag = f"  *** IP={ip.group(1)}!" if ip else ""
            print(f"  << [{label}] {data.hex()} | {repr(txt)}{flag}")
        return h

    async with BleakClient(device) as client:
        await asyncio.sleep(1.0)

        for uuid, label in [(DE5B_NOTIFY,"de5b"),(AE02_NOTIFY,"ae02")]:
            try: await client.start_notify(uuid, handler(label)); print(f"  O {label}")
            except: pass

        print()

        # 1. aa55 형식으로 ae01에 0x40
        print("[1] aa55 0x40 → ae01...")
        await client.write_gatt_char(AE01_WRITE, pkt_aa55(0x40))
        await asyncio.sleep(5.0)

        # 2. aa55 형식으로 UART에 0x40
        print("[2] aa55 0x40 → Nordic UART (6e400002)...")
        try:
            await client.write_gatt_char(UART_WRITE, pkt_aa55(0x40))
            await asyncio.sleep(5.0)
        except Exception as e: print(f"  X {e}")

        # 3. de5b 채널에 aa55 형식
        print("[3] aa55 0x40 → de5b Write...")
        try:
            await client.write_gatt_char(DE5B_WRITE, pkt_aa55(0x40))
            await asyncio.sleep(5.0)
        except Exception as e: print(f"  X {e}")

        # 4. de5b 채널에 bc73 형식 (관찰된 프로토콜 역방향)
        # bc73 + cmd + data 형식 추측
        print("[4] bc73 형식으로 de5b에 전송 시도...")
        for payload in [
            bytes([0xBC, 0x73, 0x40, 0x00]),       # bc73 + 0x40
            bytes([0xBC, 0x73, 0x06, 0x00]),       # bc73 + 0x06 (transfer?)
            bytes([0xBC, 0x73, 0x02, 0x01, 0x40]), # bc73 type02 + 0x40
        ]:
            try:
                await client.write_gatt_char(DE5B_WRITE, payload)
                print(f"  O 전송: {payload.hex()}")
                await asyncio.sleep(3.0)
            except Exception as e: print(f"  X {e}")

        # Wi-Fi 변화 확인
        after = get_ssids()
        new = after - before
        if new: print(f"\n  *** 새 SSID: {new}")

    print(f"\nBLE IP: {found_ip[0] or '없음'}")
    print(f"새 Wi-Fi: {after - before or '없음'}")
    print(f"전체 수신 {len(all_rx)}개")

asyncio.run(main())

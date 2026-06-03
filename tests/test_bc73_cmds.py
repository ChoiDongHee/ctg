"""
de5b 채널 bc73 형식 명령 체계적 탐색
de5bf72a Write → de5bf729 Notify

Run: python test_bc73_cmds.py
"""
import asyncio, sys, subprocess, re, time
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

DE5B_WRITE  = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
DE5B_NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"
AE01_WRITE  = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"

IP_RE = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

def get_ssids():
    try:
        r = subprocess.run(["netsh","wlan","show","networks"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return set(re.findall(r"SSID\s+\d+\s*:\s*(.+)", r.stdout))
    except: return set()

def bc73_pkt(*bytes_):
    """bc73 헤더 + 바이트들"""
    return bytes([0xBC, 0x73]) + bytes(bytes_)

def aa55_pkt(cmd, payload=b""):
    body = bytes([cmd, len(payload)]) + payload
    return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

async def send_and_wait(client, write_uuid, data, rx, wait=2.0, label=""):
    rx.clear()
    before_ssids = get_ssids()
    try:
        await client.write_gatt_char(write_uuid, data)
        print(f"  O [{label}] 전송: {data.hex()}")
    except Exception as e:
        print(f"  X [{label}] 실패: {e}")
        return

    await asyncio.sleep(wait)

    if rx:
        for ch, d in rx:
            txt = d.decode("utf-8", errors="replace")
            ip = IP_RE.search(txt)
            print(f"  << [{ch}] {d.hex()}  |  {repr(txt)}" + (f"  *** IP={ip.group(1)}!" if ip else ""))
    else:
        # Wi-Fi 변화 확인
        new = get_ssids() - before_ssids
        if new:
            print(f"  *** 새 Wi-Fi SSID: {new}")
        else:
            print(f"  - 응답 없음")

async def main():
    print("=" * 60)
    print("de5b bc73 명령 체계적 탐색")
    print("=" * 60)

    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev: print("X 못 찾음 - A1 버튼으로 안경 켜주세요"); return
    print(f"\nO {dev.name} ({dev.address})\n")

    rx = []
    def handler(ch_label):
        def h(s, d): rx.append((ch_label, d))
        return h

    async with BleakClient(dev) as client:
        await asyncio.sleep(1.0)
        await client.start_notify(DE5B_NOTIFY, handler("de5b"))
        await client.start_notify(AE02_NOTIFY, handler("ae02"))
        print("O Notify 구독 완료\n")

        before_wifi = get_ssids()

        # ── 1. de5b 채널 + aa55 형식 (현재 방식) ──────────
        print("[1] de5b Write + aa55 형식 사진 촬영 (0x01)")
        await send_and_wait(client, DE5B_WRITE, aa55_pkt(0x01), rx, 3.0, "de5b+aa55 photo")

        print("\n[2] de5b Write + aa55 형식 Wi-Fi (0x40)")
        await send_and_wait(client, DE5B_WRITE, aa55_pkt(0x40), rx, 8.0, "de5b+aa55 wifi")

        # ── 2. de5b 채널 + bc73 형식 다양한 시도 ──────────
        print("\n[3] de5b Write + bc73 형식 (type=01, cmd=01 사진?)")
        await send_and_wait(client, DE5B_WRITE, bc73_pkt(0x01, 0x01), rx, 3.0, "bc73 0101")

        print("\n[4] de5b Write + bc73 형식 (type=06, 전송모드?)")
        await send_and_wait(client, DE5B_WRITE, bc73_pkt(0x06, 0x00), rx, 3.0, "bc73 0600")

        print("\n[5] de5b Write + bc73 형식 (type=01, cmd=06 Wi-Fi?)")
        await send_and_wait(client, DE5B_WRITE, bc73_pkt(0x01, 0x06), rx, 8.0, "bc73 0106 wifi")

        print("\n[6] de5b Write + bc73 형식 (type=01, cmd=40 Wi-Fi?)")
        await send_and_wait(client, DE5B_WRITE, bc73_pkt(0x01, 0x40), rx, 8.0, "bc73 0140 wifi")

        print("\n[7] de5b Write + 짧은 명령 (단일 바이트들)")
        for b in [0x01, 0x06, 0x40, 0x10, 0x20, 0x21]:
            await send_and_wait(client, DE5B_WRITE, bytes([b]), rx, 1.5, f"single 0x{b:02X}")

        # ── 3. ae01 + aa55 (기존 방식 재확인) ─────────────
        print("\n[8] ae01 Write + aa55 사진 (0x01) — ae02 에코 비교")
        await send_and_wait(client, AE01_WRITE, aa55_pkt(0x01), rx, 3.0, "ae01+aa55 photo")

        # ── 4. Wi-Fi 최종 결과 ─────────────────────────────
        after_wifi = get_ssids()
        new = after_wifi - before_wifi
        print(f"\n=== Wi-Fi 변화: {new or '없음'} ===")

    print("\n=== 완료 ===")

asyncio.run(main())

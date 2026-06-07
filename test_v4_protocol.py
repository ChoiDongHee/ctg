"""
HeyCyan MO2E v4 프로토콜 테스트
형식: BC 41 [LEN_LO] [LEN_HI] [PAYLOAD] [CRC16_LO] [CRC16_HI]
채널: 6e400002 Write / 6e400003 Notify (Nordic UART)
참고: HeyCyan_MO2E_CLI_개발_분석_v4-1.md
"""
import asyncio, sys, time, os
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bleak import BleakScanner, BleakClient

WRITE_UUID  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART RX
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART TX
LOG_FILE    = "notify_log.txt"


# ── 프로토콜 ──────────────────────────────────────────
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def build_packet(payload: bytes, cmd: int = 0x41) -> bytes:
    """BC [CMD] [LEN_LO] [LEN_HI] [PAYLOAD] [CRC16_LO] [CRC16_HI]"""
    length = len(payload).to_bytes(2, "little")
    crc = crc16_modbus(payload).to_bytes(2, "little")
    return bytes([0xBC, cmd]) + length + payload + crc


COMMANDS = {
    "photo":         bytes.fromhex("020101"),
    "video-start":   bytes.fromhex("020102"),
    "video-stop":    bytes.fromhex("020103"),
    "audio-start":   bytes.fromhex("020108"),
    "audio-stop":    bytes.fromhex("02010C"),
    "wifi-ble":      bytes.fromhex("02010401"),
    "wifi-ap":       bytes.fromhex("02010402"),
    "preview-start": bytes.fromhex("02011402"),
    "preview-stop":  bytes.fromhex("02011501"),
    "album-count":   bytes.fromhex("0204"),
    "restart":       bytes.fromhex("02010E"),
}


# ── 로그 ──────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_bc_response(data: bytes) -> dict:
    if len(data) < 4 or data[0] != 0xBC:
        return {"type": "unknown", "raw": data.hex(" ").upper()}
    cmd = data[1]
    length = int.from_bytes(data[2:4], "little")
    payload = data[4:4+length] if len(data) >= 4+length else data[4:]
    crc = data[4+length:4+length+2] if len(data) >= 4+length+2 else b""
    return {
        "type": "bc_response",
        "cmd": f"0x{cmd:02X}",
        "length": length,
        "payload": payload.hex(" ").upper(),
        "crc": crc.hex(" ").upper(),
    }


def on_notify(sender, data: bytearray):
    raw = bytes(data)
    hex_str = raw.hex(" ").upper()

    parsed = parse_bc_response(raw)
    if parsed["type"] == "bc_response":
        log(f"NOTIFY BC: cmd={parsed['cmd']} len={parsed['length']} payload={parsed['payload']} crc={parsed['crc']}", "RECV")
    else:
        log(f"NOTIFY RAW: {hex_str}", "RECV")


# ── 연결 ──────────────────────────────────────────────
async def connect_with_retry(address: str, attempts: int = 4) -> BleakClient:
    for i in range(attempts):
        try:
            client = BleakClient(address, timeout=60.0)
            await asyncio.wait_for(client.connect(timeout=60.0), timeout=65.0)
            return client
        except Exception as e:
            log(f"연결 시도 {i+1}/{attempts} 실패: {type(e).__name__}", "WARN")
            try: await client.disconnect()
            except: pass
            await asyncio.sleep(5)
    raise RuntimeError("연결 실패")


# ── 메인 ──────────────────────────────────────────────
async def main():
    # 로그 파일 시작
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"세션 시작: {datetime.now()}\n")
        f.write(f"프로토콜: BC 41 [LEN_LO LEN_HI] [PAYLOAD] [CRC16]\n")
        f.write(f"{'='*60}\n")

    log("=== MO2E v4 프로토콜 테스트 ===")
    log(f"로그 파일: {os.path.abspath(LOG_FILE)}")

    # 스캔
    log("M0* 기기 스캔 중 (12초)...")
    devices = await BleakScanner.discover(timeout=12.0)
    dev = next((d for d in devices if (d.name or "").lower().startswith("m0")), None)
    if not dev:
        log("X 못 찾음 — 안경 켜주세요", "ERROR")
        return
    log(f"O 발견: {dev.name} ({dev.address})")

    # 연결
    log("연결 중...")
    client = await connect_with_retry(dev.address)
    log(f"O 연결 성공! MTU={client.mtu_size}")

    try:
        await asyncio.sleep(1.0)

        # Notify 구독 (Nordic UART TX)
        await client.start_notify(NOTIFY_UUID, on_notify)
        log(f"O Notify 구독: {NOTIFY_UUID}")
        await asyncio.sleep(0.5)

        # 테스트 시퀀스
        tests = [
            ("album-count",  "앨범 개수 조회",   3.0),
            ("photo",        "사진 촬영",         5.0),
            ("album-count",  "촬영 후 개수 확인", 3.0),
            ("video-start",  "영상 녹화 시작",    5.0),
            ("video-stop",   "영상 녹화 중지",    3.0),
            ("wifi-ap",      "Wi-Fi AP 모드",     10.0),
        ]

        for cmd_name, desc, wait in tests:
            payload = COMMANDS[cmd_name]
            pkt = build_packet(payload)

            log(f"--- {desc} ---")
            log(f"WRITE: {pkt.hex(' ').upper()} (response=False)")

            try:
                await client.write_gatt_char(WRITE_UUID, pkt, response=False)
                log(f"O 전송 완료 ({len(pkt)} bytes)")
            except Exception as e:
                log(f"X 전송 실패: {e}", "ERROR")
                # response=True 로 재시도
                try:
                    await client.write_gatt_char(WRITE_UUID, pkt, response=True)
                    log(f"O response=True 로 재시도 성공")
                except Exception as e2:
                    log(f"X response=True 도 실패: {e2}", "ERROR")
                    continue

            log(f"  {wait:.0f}초 대기...")
            await asyncio.sleep(wait)

        log("=== 테스트 완료 ===")
        log(f"전체 로그: {os.path.abspath(LOG_FILE)}")

    except Exception as e:
        log(f"오류: {e}", "ERROR")
    finally:
        if client.is_connected:
            await client.stop_notify(NOTIFY_UUID)
            await client.disconnect()
        log("연결 종료")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"세션 종료: {datetime.now()}\n\n")


asyncio.run(main())

import asyncio
import re
import time
from enum import Enum
from typing import Callable, Optional

from bleak import BleakScanner, BleakClient

# ── v4 프로토콜 확정 UUID (HeyCyan_MO2E_CLI_개발_분석_v4-1.md) ──
# Nordic UART — 실제 명령 채널
WRITE_CHAR   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART RX (Write)
NOTIFY_CHAR  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART TX (Notify)

# Serial Port (bc73 heartbeat/상태용)
SERIAL_SVC   = "de5bf728-d711-4e47-af26-65e3012a5dc7"
SERIAL_WRITE = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
SERIAL_NOTIFY= "de5bf729-d711-4e47-af26-65e3012a5dc7"

# ae30 서비스 (에코 채널)
AE01_WRITE   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_NOTIFY  = "0000ae02-0000-1000-8000-00805f9b34fb"

# nRF Connect와 동일하게 구독할 9개 채널 (type=03 배터리 수신용)
ALL_NOTIFY_UUIDS = [
    AE02_NOTIFY,
    "0000ae04-0000-1000-8000-00805f9b34fb",
    "0000ae05-0000-1000-8000-00805f9b34fb",
    "00004a02-0000-1000-8000-00805f9b34fb",
    "0000ae3c-0000-1000-8000-00805f9b34fb",
    "00002a05-0000-1000-8000-00805f9b34fb",
    NOTIFY_CHAR,    # Nordic UART TX
    SERIAL_NOTIFY,  # Serial Port (bc73)
    "0000fee3-0000-1000-8000-00805f9b34fb",
]

_IP_REGEX = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
    r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
)


class BLEState(str, Enum):
    DISCONNECTED = "disconnected"
    SCANNING     = "scanning"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"


class BLEManager:
    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device_address: Optional[str] = None
        self.state = BLEState.DISCONNECTED
        self.battery_level: Optional[int] = None
        self.battery_charging: bool = False
        self.media_count: dict = {"photo": 0, "video": 0, "audio": 0}
        self.firmware_version: Optional[str] = None
        self.extracted_ip: Optional[str] = None
        self._wifi_ssid: Optional[str] = None
        self._wifi_password: Optional[str] = None
        self._on_notify: Optional[Callable] = None

    @property
    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    # ── v5 응답 파싱 ─────────────────────────────────────
    def _parse_bc73(self, data: bytes):
        """
        v5 응답 포맷: BC [CMD] [LEN_LO LEN_HI] [CRC_LO CRC_HI] [PAYLOAD]
        CMD=0x73: 자동 상태 브로드캐스트 (배터리, 센서)
        CMD=0x41: 명령 응답 (앨범개수, 모드변경 등)
        """
        if len(data) < 7 or data[0] != 0xBC:
            return

        cmd = data[1]
        length = int.from_bytes(data[2:4], "little")
        payload = data[6:6+length] if len(data) >= 6+length else data[6:]

        # IP 추출
        try:
            text = data.decode("utf-8", errors="ignore")
            m = _IP_REGEX.search(text)
            if m:
                self.extracted_ip = m.group()
        except Exception:
            pass

        # 배터리 파싱: payload[0]=0x05 → [1]=배터리%, [2]=충전상태
        if payload and len(payload) >= 3 and payload[0] == 0x05:
            level = payload[1]
            if 0 <= level <= 100:
                self.battery_level = level
                self.battery_charging = bool(payload[2])

        # 앨범 개수 응답 (CMD=0x41, payload starts with 02 04)
        if cmd == 0x41 and len(payload) >= 3 and payload[0] == 0x02 and payload[1] == 0x04:
            self.media_count = {
                "photo": payload[2],
                "video": payload[3] if len(payload) > 3 else 0,
                "audio": payload[4] if len(payload) > 4 else 0,
            }

        # SSID/PW 파싱 시도
        if len(data) > 6:
            try:
                for idx in range(4, len(data) - 1):
                    slen = data[idx]
                    if 3 <= slen <= 32 and idx + slen < len(data):
                        candidate = data[idx+1:idx+1+slen].decode("ascii", errors="ignore")
                        if all(c.isprintable() for c in candidate) and len(candidate) >= 3:
                            pw_idx = idx + 1 + slen
                            if pw_idx < len(data):
                                plen = data[pw_idx]
                                if 0 <= plen <= 32 and pw_idx + plen <= len(data):
                                    pw = data[pw_idx+1:pw_idx+1+plen].decode("ascii", errors="ignore")
                                    self._wifi_ssid = candidate
                                    self._wifi_password = pw
                                    break
                    idx += 1
            except Exception:
                pass

    def _on_ble_notify(self, sender, data: bytes):
        self._parse_bc73(data)
        if self._on_notify:
            self._on_notify(data)

    # ── 스캔 ──────────────────────────────────────────────
    async def scan(self, timeout: float = 15.0) -> list:
        self.state = BLEState.SCANNING
        try:
            devices = await BleakScanner.discover(timeout=timeout)
            def priority(d):
                name = (d.name or "").lower()
                if name.startswith("m0"): return 0
                if "cyan" in name or name.startswith("g"): return 1
                if d.name and d.name != "Unknown": return 2
                return 3
            return sorted(devices, key=priority)
        finally:
            self.state = BLEState.DISCONNECTED

    # ── 연결 ──────────────────────────────────────────────
    async def connect(self, address: str, on_notify: Callable = None) -> bool:
        self.state = BLEState.CONNECTING
        self._on_notify = on_notify
        try:
            # Windows 안정성: 스캔으로 기기 객체 확보 후 연결
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if device is None:
                raise RuntimeError(f"기기 못 찾음 ({address})")

            self.client = BleakClient(device, disconnected_callback=self._on_disconnect)
            await self.client.connect(timeout=15.0)
            await asyncio.sleep(1.0)

            # 페어링 시도 (Bonded 연결 — type=03 수신에 필요할 수 있음)
            try:
                await self.client.pair()
            except Exception:
                pass  # 이미 페어링됐거나 불필요한 경우 무시

            # nRF Connect와 동일하게 9개 채널 모두 구독
            for uuid in ALL_NOTIFY_UUIDS:
                try:
                    await self.client.start_notify(uuid, self._on_ble_notify)
                except Exception:
                    pass

            self.device_address = address
            self.state = BLEState.CONNECTED

            # 연결 직후 배터리 + 미디어 개수 조회
            await asyncio.sleep(0.5)
            await self.get_battery()
            await asyncio.sleep(0.5)
            await self.get_media_count()
            return True

        except Exception:
            self.state = BLEState.DISCONNECTED
            self.client = None
            raise

    def _on_disconnect(self, client):
        self.state = BLEState.DISCONNECTED

    async def disconnect(self):
        if self.client and self.client.is_connected:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.state = BLEState.DISCONNECTED
        self.device_address = None

    # ── v4 패킷 빌더 (BC 41 CRC16 프로토콜) ─────────────
    @staticmethod
    def _crc16(data: bytes) -> int:
        """CRC16 Modbus (Init=0xFFFF, Poly=0xA001, LE output)"""
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

    def _build_v5(self, payload: bytes, cmd: int = 0x41) -> bytes:
        """
        v5 확정 포맷: BC [CMD] [LEN_LO] [LEN_HI] [CRC_LO] [CRC_HI] [PAYLOAD]
        CRC가 PAYLOAD 앞에 옴 (v4와 순서 다름)
        WRITE: de5bf72a / response=False
        """
        length = len(payload).to_bytes(2, "little")
        crc = self._crc16(payload).to_bytes(2, "little")
        return bytes([0xBC, cmd]) + length + crc + payload

    # v4 호환 (deprecated)
    def _build_v4(self, payload: bytes, cmd: int = 0x41) -> bytes:
        return self._build_v5(payload, cmd)

    # ── 기기 제어 명령 (v4 프로토콜) ─────────────────────
    async def _cmd(self, *payload_bytes: int) -> str:
        """
        v5 확정: de5bf72a Write에 BC [CMD] [LEN] [CRC] [PAYLOAD] 전송
        response=False (write without response)
        """
        if not self.is_connected:
            raise RuntimeError("BLE 연결 안 됨")
        payload = bytes(payload_bytes)
        pkt = self._build_v5(payload)
        await self.client.write_gatt_char(WRITE_CHAR, pkt, response=False)
        return pkt.hex(" ").upper()

    async def take_photo(self):
        """사진 촬영: BC 41 03 00 02 01 01 CRC16"""
        return await self._cmd(0x02, 0x01, 0x01)

    async def start_video(self):
        """영상 녹화 시작: BC 41 03 00 02 01 02 CRC16"""
        return await self._cmd(0x02, 0x01, 0x02)

    async def stop_video(self):
        """영상 녹화 중지: BC 41 03 00 02 01 03 CRC16"""
        return await self._cmd(0x02, 0x01, 0x03)

    async def enable_wifi_transfer(self) -> Optional[str]:
        """Wi-Fi AP 모드: BC 41 04 00 02 01 04 02 CRC16"""
        self.extracted_ip = None
        self._wifi_ssid = None
        await self._cmd(0x02, 0x01, 0x04, 0x02)  # AP 앨범 가져오기 (Wi-Fi AP 모드)

        for _ in range(150):
            await asyncio.sleep(0.1)
            if self.extracted_ip or self._wifi_ssid:
                return self.extracted_ip or self._wifi_ssid
        return None

    async def get_media_count(self):
        """앨범 개수 조회: BC 41 02 00 02 04 CRC16"""
        return await self._cmd(0x02, 0x04)

    async def get_battery(self):
        """배터리 조회 (bc73 type=03으로 자동 수신됨, 강제 폴링 불필요)"""
        try:
            await self._cmd(0x02, 0x04)  # 앨범 조회로 keepalive
        except Exception:
            pass
        try:
            await self.client.write_gatt_char(
                AE01_WRITE,
                bytes([0xAA, 0x55, 0x20, 0x00, 0x20]),
                response=False
            )
        except Exception:
            pass
        # de5b는 이제 keepalive만 사용


    async def start_audio(self):
        """오디오 녹음 시작: [0x02, 0x01, 0x08]"""
        return await self._cmd(0x02, 0x01, 0x08)

    async def stop_audio(self):
        """오디오 녹음 중지: [0x02, 0x01, 0x0C]"""
        return await self._cmd(0x02, 0x01, 0x0C)

    async def trigger_ai_photo(self):
        """AI 이미지 생성: [0x02, 0x01, 0x06, 0x02, 0x02, 0x02]"""
        return await self._cmd(0x02, 0x01, 0x06, 0x02, 0x02, 0x02)

    async def print_all_uuids(self) -> list:
        if not self.is_connected:
            raise RuntimeError("연결 안 됨")
        result = []
        for svc in self.client.services:
            svc_info = {"uuid": str(svc.uuid), "chars": []}
            for ch in svc.characteristics:
                char_info = {"uuid": str(ch.uuid), "properties": list(ch.properties)}
                if "read" in ch.properties:
                    try:
                        val = await self.client.read_gatt_char(ch.uuid)
                        char_info["value"] = val.hex()
                    except Exception:
                        pass
                svc_info["chars"].append(char_info)
            result.append(svc_info)
        return result

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "address": self.device_address,
            "battery": self.battery_level,
            "battery_charging": self.battery_charging,
            "media_count": self.media_count,
            "firmware": self.firmware_version,
            "wifi_ip": self.extracted_ip,
            "wifi_ssid": self._wifi_ssid,
        }

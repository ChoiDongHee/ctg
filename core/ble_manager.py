import asyncio
import re
import time
from enum import Enum
from typing import Callable, Optional

from bleak import BleakScanner, BleakClient

# ── 확인된 UUID (nRF Connect + AAR 디컴파일) ─────────────
# Serial Port Service (실제 데이터 채널)
SERIAL_SVC   = "de5bf728-d711-4e47-af26-65e3012a5dc7"
WRITE_CHAR   = "de5bf72a-d711-4e47-af26-65e3012a5dc7"  # SERIAL_PORT_CHARACTER_WRITE
NOTIFY_CHAR  = "de5bf729-d711-4e47-af26-65e3012a5dc7"  # SERIAL_PORT_CHARACTER_NOTIFY

# Nordic UART (Constants.class: UUID_SERVICE/UUID_WRITE/UUID_READ)
UART_SVC     = "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e"
UART_WRITE   = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # UUID_WRITE (RX)
UART_READ    = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # UUID_READ  (TX)

# ae30 서비스 (에코 채널 — aa55 명령 → ae02 에코)
AE01_WRITE   = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02_NOTIFY  = "0000ae02-0000-1000-8000-00805f9b34fb"

# nRF Connect와 동일하게 구독할 9개 채널
ALL_NOTIFY_UUIDS = [
    AE02_NOTIFY,
    "0000ae04-0000-1000-8000-00805f9b34fb",
    "0000ae05-0000-1000-8000-00805f9b34fb",
    "00004a02-0000-1000-8000-00805f9b34fb",
    "0000ae3c-0000-1000-8000-00805f9b34fb",
    "00002a05-0000-1000-8000-00805f9b34fb",
    UART_READ,
    NOTIFY_CHAR,
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

    # ── bc73 응답 파싱 ────────────────────────────────────
    def _parse_bc73(self, data: bytes):
        if len(data) < 4 or data[0] != 0xBC or data[1] != 0x73:
            return

        # IP 추출 (Wi-Fi 활성화 응답)
        try:
            text = data.decode("utf-8", errors="ignore")
            m = _IP_REGEX.search(text)
            if m:
                self.extracted_ip = m.group()
        except Exception:
            pass

        resp_type = data[2]

        # type=03: 상태 업데이트 (배터리, 미디어수 등)
        # Android: loadData[6]=0x05 → battery=loadData[7], charging=loadData[8]
        if resp_type == 0x03 and len(data) >= 8:
            for i in range(4, len(data) - 2):
                if data[i] == 0x05 and i + 2 < len(data):
                    level = data[i + 1]
                    if 0 <= level <= 100:
                        self.battery_level = level
                        self.battery_charging = bool(data[i + 2])
                    break

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

    # ── 명령 전송 헬퍼 ────────────────────────────────────
    async def _write(self, uuid: str, data: bytes):
        if not self.is_connected:
            raise RuntimeError("BLE 연결 안 됨")
        await self.client.write_gatt_char(uuid, data)

    def _aa55(self, cmd: int, *payload: int) -> bytes:
        body = bytes([cmd, len(payload)]) + bytes(payload)
        return bytes([0xAA, 0x55]) + body + bytes([sum(body) & 0xFF])

    # ── 기기 제어 명령 ────────────────────────────────────
    # Android SDK 확인: LargeDataHandler.glassesControl(byteArrayOf(...))
    # 세 채널 동시 시도로 동작 확률 극대화

    async def _cmd(self, *raw_bytes: int):
        """de5b + ae01 + UART 세 채널 동시 전송"""
        raw = bytes(raw_bytes)
        aa = self._aa55(raw_bytes[0], *raw_bytes[1:]) if len(raw_bytes) >= 1 else raw

        results = []
        for uuid, data in [
            (WRITE_CHAR,  raw),   # Serial Port (SDK 확인)
            (AE01_WRITE,  aa),    # ae30 서비스 (에코 확인)
            (UART_WRITE,  raw),   # Nordic UART
        ]:
            try:
                await self._write(uuid, data)
                results.append(uuid[:8])
            except Exception:
                pass
        return results

    async def take_photo(self):
        """사진 촬영: [0x02, 0x01, 0x01]"""
        return await self._cmd(0x02, 0x01, 0x01)

    async def start_video(self):
        """영상 녹화 시작: [0x02, 0x01, 0x02]"""
        return await self._cmd(0x02, 0x01, 0x02)

    async def stop_video(self):
        """영상 녹화 중지: [0x02, 0x01, 0x03]"""
        return await self._cmd(0x02, 0x01, 0x03)

    async def enable_wifi_transfer(self) -> Optional[str]:
        """Wi-Fi 전송 모드: [0x02, 0x01, 0x04] + aa55 0x40"""
        self.extracted_ip = None
        self._wifi_ssid = None

        # 두 형식 모두 전송
        await self._cmd(0x02, 0x01, 0x04)
        try:
            await self._write(AE01_WRITE, self._aa55(0x40))
        except Exception:
            pass

        # IP/SSID 수신 대기 (최대 15초)
        for _ in range(150):
            await asyncio.sleep(0.1)
            if self.extracted_ip or self._wifi_ssid:
                return self.extracted_ip or self._wifi_ssid
        return None

    async def get_media_count(self):
        """미디어 파일 개수: [0x02, 0x04]"""
        await self._cmd(0x02, 0x04)

    async def get_battery(self):
        """배터리 조회 — ae01 aa55 + Serial Port 동시"""
        try:
            await self._write(AE01_WRITE, self._aa55(0x20))
        except Exception:
            pass
        try:
            await self._write(WRITE_CHAR, bytes([0x02, 0x04]))
        except Exception:
            pass

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

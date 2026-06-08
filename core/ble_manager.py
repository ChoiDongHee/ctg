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
        self.last_ack_cmd: Optional[int] = None
        self.last_photo_bytes: Optional[bytes] = None
        self._photo_buf: dict = {}

    @property
    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    # ── BC 프레임 전체 파서 (로그 분석으로 확인) ────────────
    def _parse_bc73(self, data: bytes):
        if len(data) < 2 or data[0] != 0xBC:
            return

        frame_type = data[1]

        # BC-73: 상태/배터리/미디어/IP
        if frame_type == 0x73 and len(data) >= 3:
            resp_type = data[2]

            # type=03: 배터리 (10초 주기 자동)
            if resp_type == 0x03 and len(data) >= 8:
                for i in range(4, len(data) - 2):
                    if data[i] == 0x05 and i + 2 < len(data):
                        level = data[i + 1]
                        if 0 <= level <= 100:
                            self.battery_level = level
                            self.battery_charging = bool(data[i + 2])
                        break

            # type=05: 미디어 개수 또는 IP 보고
            elif resp_type == 0x05 and len(data) >= 11:
                # BC-73-05-00-[ck]-[ck]-08-[IP1]-[IP2]-[IP3]-[IP4]
                if data[6] == 0x08:
                    ip_bytes = data[7:11]
                    self.extracted_ip = ".".join(map(str, ip_bytes))
                # BC-73-05-00-[ck]-[ck]-02-00-[photos]-[videos]-[audio]
                elif data[6] == 0x02:
                    self.media_count["photo"] = data[8]
                    self.media_count["video"] = data[9]
                    self.media_count["audio"] = data[10]

        # BC-41: 명령 ACK & Wi-Fi 설정
        elif frame_type == 0x41 and len(data) >= 9:
            # Wi-Fi 설정 응답 (BC-41-22-00-...-02-01-04-01-[ssid_len]-[pwd_len]-[SSID]-[PWD])
            if len(data) >= 14 and data[6:9] == b"\x02\x01\x04":
                try:
                    import struct
                    ssid_len = struct.unpack("<H", data[10:12])[0]
                    pwd_len = struct.unpack("<H", data[12:14])[0]
                    self._wifi_ssid = data[14:14+ssid_len].decode("ascii", errors="ignore")
                    self._wifi_password = data[14+ssid_len:14+ssid_len+pwd_len].decode("ascii", errors="ignore")
                except Exception: pass
            
            # 일반 명령 ACK
            if data[6] == 0x02 and data[7] == 0x01:
                cmd = data[8]
                self.last_ack_cmd = cmd

        # BC-43: 기기 정보 (펌웨어, Wi-Fi 버전 등)
        elif frame_type == 0x43 and len(data) > 8:
            try:
                info_bytes = data[8:]
                info_str = info_bytes.decode("ascii", errors="ignore")
                if "A02S" in info_str or "WIFI" in info_str:
                    self.firmware_version = info_str.strip('\x00').replace('\x00', ' ')
            except Exception: pass

        # BC-FD-FA-03: 사진 데이터 멀티패킷
        elif frame_type == 0xFD and len(data) > 11 and data[2] == 0xFA and data[3] == 0x03:
            # 헤더: BC FD FA 03 [ck1] [ck2] 01 [total] [seq] 00 00 [jpeg_data...]
            total = data[7]
            seq = data[8]
            jpeg_data = data[11:]
            
            if not hasattr(self, '_photo_buf'):
                self._photo_buf = {}
            
            self._photo_buf[seq] = jpeg_data
            
            # 전체 수신 완료 시 조립
            if len(self._photo_buf) >= total and total > 0:
                full = b"".join(self._photo_buf[i] for i in sorted(self._photo_buf.keys()))
                self.last_photo_bytes = full
                self._photo_buf = {}


    def _on_ble_notify(self, sender, data: bytes):
        try:
            self._parse_bc73(data)
        except Exception:
            pass
        try:
            if self._on_notify:
                self._on_notify(data)
        except Exception:
            pass

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
            # Windows: 스캔으로 BLE 스택 초기화 후 직접 연결
            # 페어링된 기기는 광고를 안 하므로 스캔에서 못 찾아도 연결 시도
            try:
                await BleakScanner.discover(timeout=10.0)
            except Exception:
                pass
            self.client = BleakClient(address, disconnected_callback=self._on_disconnect)
            await self.client.connect(timeout=60.0)
            await asyncio.sleep(1.0)

            # pair()는 Windows에서 연결 끊김 유발 가능 — 생략
            # (이미 시스템에 페어링됨)  # 이미 페어링됐거나 불필요한 경우 무시

            # nRF Connect와 동일하게 9개 채널 모두 구독
            subscribed = []
            for uuid in ALL_NOTIFY_UUIDS:
                try:
                    await self.client.start_notify(uuid, self._on_ble_notify)
                    subscribed.append(uuid[-8:])
                except Exception as e:
                    pass
            import sys
            print(f"[BLE] 구독 성공: {subscribed}", file=sys.stderr)

            self.device_address = address
            self.state = BLEState.CONNECTED

            # bc73 type=03이 10초마다 자동 수신되므로 별도 조회 불필요
            # (연결 직후 write 명령은 연결 끊김 유발 가능)
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
    def _crc16_ibm(self, data: bytes) -> int:
        """IBM CRC16 (Poly 0x8005, Init 0xFFFF, RefIn/Out True)"""
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    def _full_frame(self, frame_type: int, payload: bytes) -> bytes:
        """공식 SDK 풀 프레임: BC [Type] [LenLo] [LenHi] [CRCLo] [CRCHi] [Payload]"""
        length = len(payload)
        crc = self._crc16_ibm(payload)
        header = bytes([0xBC, frame_type, length & 0xFF, (length >> 8) & 0xFF, crc & 0xFF, (crc >> 8) & 0xFF])
        return header + payload

    async def _cmd(self, *raw_bytes: int):
        """명령 전송: 공식 풀 프레임 (Type 0x02) → de5b 우선"""
        # 로그 분석 결과 명령은 보통 5바이트 (02 01 XX FF FF)
        payload = list(raw_bytes)
        if len(payload) == 3:
            payload += [0xFF, 0xFF]
        
        payload_bytes = bytes(payload)
        full = self._full_frame(0x02, payload_bytes) # 명령은 항상 Type 0x02?
        
        # 보조 채널용 aa55
        aa = self._aa55(payload[0], *payload[1:])
        
        results = []
        try:
            # de5b 채널에 공식 풀 프레임 전송
            await self._write(WRITE_CHAR, full)
            results.append("de5b_full")
        except Exception: pass
        
        try:
            # 보조 채널 (ae01)
            await self._write(AE01_WRITE, aa)
            results.append("ae01")
        except Exception: pass
        
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

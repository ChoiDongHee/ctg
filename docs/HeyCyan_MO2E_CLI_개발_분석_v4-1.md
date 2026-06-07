# HeyCyan / MO2E CLI 개발 분석 v4

> 목적: HeyCyan 앱/APK와 기존 Wi-Fi 패킷 로그를 바탕으로, 노트북에서 Python CLI로 스마트 안경을 제어하기 위한 개발 명세를 정리한다.  
> 대상 기능: BLE 연결, 사진 촬영, 영상 시작/정지, 음성 시작/정지, Notify 응답 수신, Wi-Fi RTP 미디어 수신 준비.

---

## 0. 현재 결론

현재 정보만으로 **1차 CLI 개발은 가능**하다.

가능한 1차 목표:

```bash
heycyan scan
heycyan connect
heycyan photo
heycyan video-start
heycyan audio-start
heycyan preview-start
heycyan preview-stop
heycyan notify-log
```

핵심 구조는 다음과 같다.

```text
Python CLI
  |
  | BLE Write
  v
HeyCyan / MO2E 안경
  |
  | BLE Notify
  v
Python CLI 응답 수신

Wi-Fi 쪽
  |
  | UDP / RTP
  v
영상/음성/미리보기 스트림 수신
```

중요한 정정:

이전 Wi-Fi 로그의 `80 23 B9 02`, `80 23 B9 03` 같은 패턴은 제조사 BLE 명령 헤더가 아니라 **RTP 미디어 스트림 헤더**로 보는 것이 더 정확하다.

즉 다음처럼 나누어야 한다.

```text
BLE = 제어 명령
Wi-Fi UDP/RTP = 영상/음성/미리보기 미디어 데이터
```

---

## 1. APK에서 확인된 핵심 구조

HeyCyan APK 내부에는 안경 제어 SDK로 보이는 패키지가 포함되어 있다.

```text
com.oudmon.ble.base.communication
```

핵심 클래스 후보:

```text
com.oudmon.ble.base.communication.LargeDataHandler
com.oudmon.ble.base.communication.utils.CRC16
```

분석 포인트:

```text
LargeDataHandler
  - glassesControl()
  - addHeader()
  - 명령 payload를 최종 BLE 패킷으로 래핑

CRC16
  - calcCrc16()
  - payload CRC 계산
```

---

## 2. BLE UUID

현재 CLI 개발에 필요한 UUID는 다음과 같다.

```text
Service UUID
6e40fff0-b5a3-f393-e0a9-e50e24dcca9e

Write Characteristic UUID
6e400002-b5a3-f393-e0a9-e50e24dcca9e

Notify Characteristic UUID
6e400003-b5a3-f393-e0a9-e50e24dcca9e
```

역할:

```text
Write UUID
  - CLI에서 안경으로 명령 전송

Notify UUID
  - 안경에서 CLI로 응답 수신
```

주의:

```text
write_gatt_char(..., response=True)
```

의 response는 BLE 전송 레벨 ACK이다.  
사진 촬영 성공/실패 같은 실제 장치 응답은 **Notify**로 따로 들어온다.

---

## 3. 명령 Payload 후보

APK 분석 기준으로 확인된 제어 payload 후보는 다음과 같다.

| 기능 | Payload |
|---|---|
| 사진 촬영 | `02 01 01` |
| 영상 시작 | `02 01 02` |
| 음성/녹음 시작 | `02 01 08` |
| 앨범 가져오기 | `02 01 04 01` |
| AP 앨범 가져오기 | `02 01 04 02` |
| 파일 완료 알림 | `02 01 09` |
| 공장 초기화 | `02 01 0A` |
| 재시작 | `02 01 0E` |
| 앨범 개수 조회 | `02 04` |
| 실시간 미리보기 시작 BLE | `02 01 14 01` |
| 실시간 미리보기 시작 AP | `02 01 14 02` |
| 실시간 미리보기 종료 | `02 01 15 01` |

---

## 4. 최종 BLE 패킷 포맷 후보

명령 payload가 그대로 BLE Write로 나가는 것이 아니라, `LargeDataHandler.addHeader()`에서 래핑되는 것으로 보인다.

현재 가장 유력한 포맷:

```text
BC [CMD] [LEN_LO] [LEN_HI] [PAYLOAD...] [CRC_LO] [CRC_HI]
```

각 필드 의미:

```text
BC
  - 패킷 시작 바이트

CMD
  - 명령 그룹 또는 command id
  - glassesControl()에서는 0x41 사용 가능성이 높음

LEN_LO LEN_HI
  - payload 길이
  - little-endian

PAYLOAD
  - 실제 명령 바이트
  - 예: 사진 촬영 = 02 01 01

CRC_LO CRC_HI
  - payload에 대한 CRC16
  - little-endian
```

예시:

```text
사진 촬영 payload
02 01 01

길이
03 00

CRC16
10 50

최종 패킷
BC 41 03 00 02 01 01 10 50
```

---

## 5. CRC16 계산 방식

APK 내부 `CRC16.calcCrc16` 기준으로 일반적인 CRC16 Modbus 계열과 일치하는 것으로 보인다.

현재 적용 후보:

```text
Initial value: 0xFFFF
Polynomial: 0xA001
Target: payload only
Output byte order: little-endian
```

Python 구현:

```python
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF

    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

            crc &= 0xFFFF

    return crc


def crc16_le(data: bytes) -> bytes:
    return crc16_modbus(data).to_bytes(2, "little")
```

---

## 6. 최종 패킷 후보 목록

아래 패킷은 `BC 41 LEN PAYLOAD CRC16(payload)` 기준으로 계산한 후보이다.

| 기능 | Payload | 최종 BLE Write 패킷 후보 |
|---|---|---|
| 사진 촬영 | `02 01 01` | `BC 41 03 00 02 01 01 10 50` |
| 영상 시작 | `02 01 02` | `BC 41 03 00 02 01 02 50 51` |
| 음성/녹음 시작 | `02 01 08` | `BC 41 03 00 02 01 08 D0 56` |
| 앨범 가져오기 | `02 01 04 01` | `BC 41 04 00 02 01 04 01 93 5C` |
| AP 앨범 가져오기 | `02 01 04 02` | `BC 41 04 00 02 01 04 02 D3 5D` |
| 파일 완료 알림 | `02 01 09` | `BC 41 03 00 02 01 09 11 96` |
| 공장 초기화 | `02 01 0A` | `BC 41 03 00 02 01 0A 51 97` |
| 재시작 | `02 01 0E` | `BC 41 03 00 02 01 0E 50 54` |
| 앨범 개수 조회 | `02 04` | `BC 41 02 00 02 04 01 13` |
| 실시간 미리보기 시작 BLE | `02 01 14 01` | `BC 41 04 00 02 01 14 01 9E 9C` |
| 실시간 미리보기 시작 AP | `02 01 14 02` | `BC 41 04 00 02 01 14 02 DE 9D` |
| 실시간 미리보기 종료 | `02 01 15 01` | `BC 41 04 00 02 01 15 01 9F 0C` |

주의:

위 값은 APK 로직과 CRC 계산식 기준의 **최종 후보**이다.  
실기기에서 Notify 응답을 받아 ACK가 오는지 확인해야 확정된다.

---

## 7. Python 패킷 빌더

```python
CMD_GLASSES_CONTROL = 0x41


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF

    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

            crc &= 0xFFFF

    return crc


def build_packet(payload: bytes, cmd: int = CMD_GLASSES_CONTROL) -> bytes:
    """
    HeyCyan / MO2E BLE command packet builder.

    Format:
        BC [CMD] [LEN_LO] [LEN_HI] [PAYLOAD...] [CRC_LO] [CRC_HI]
    """
    if not 0 <= cmd <= 0xFF:
        raise ValueError("cmd must be one byte")

    length = len(payload).to_bytes(2, "little")
    crc = crc16_modbus(payload).to_bytes(2, "little")

    return bytes([0xBC, cmd]) + length + payload + crc


COMMANDS = {
    "photo": bytes.fromhex("02 01 01"),
    "video-start": bytes.fromhex("02 01 02"),
    "audio-start": bytes.fromhex("02 01 08"),
    "album": bytes.fromhex("02 01 04 01"),
    "ap-album": bytes.fromhex("02 01 04 02"),
    "media-done": bytes.fromhex("02 01 09"),
    "factory-reset": bytes.fromhex("02 01 0A"),
    "restart": bytes.fromhex("02 01 0E"),
    "album-count": bytes.fromhex("02 04"),
    "preview-start-ble": bytes.fromhex("02 01 14 01"),
    "preview-start-ap": bytes.fromhex("02 01 14 02"),
    "preview-stop": bytes.fromhex("02 01 15 01"),
}


def build_command(name: str) -> bytes:
    payload = COMMANDS[name]
    return build_packet(payload)
```

테스트:

```python
print(build_command("photo").hex(" ").upper())
# BC 41 03 00 02 01 01 10 50
```

---

## 8. BLE Notify 응답 수신 구조

BLE 제어 CLI에서 가장 먼저 구현해야 할 것은 Notify 로그 저장이다.

```python
import asyncio
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


def on_notify(sender, data: bytearray):
    hex_data = data.hex(" ").upper()
    print(f"[NOTIFY] {sender}: {hex_data}")

    with open("notify_log.txt", "a", encoding="utf-8") as f:
        f.write(hex_data + "\n")

    parse_response(bytes(data))


def parse_response(data: bytes):
    if not data:
        return

    if data[0] == 0xBC:
        print("[PARSE] BC wrapped response")

        if len(data) >= 4:
            cmd = data[1]
            length = int.from_bytes(data[2:4], "little")
            payload = data[4:4 + length]
            crc = data[4 + length:4 + length + 2]

            print(f"[PARSE] cmd=0x{cmd:02X}")
            print(f"[PARSE] len={length}")
            print(f"[PARSE] payload={payload.hex(' ').upper()}")
            print(f"[PARSE] crc={crc.hex(' ').upper()}")

    else:
        print("[PARSE] unknown/raw response:", data.hex(" ").upper())
```

---

## 9. 최소 동작 테스트 코드

아래 코드는 BLE scan → connect → Notify 구독 → 사진 촬영 패킷 전송 → 응답 대기 흐름이다.

```python
import asyncio
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF

    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

            crc &= 0xFFFF

    return crc


def build_packet(payload: bytes, cmd: int = 0x41) -> bytes:
    return (
        bytes([0xBC, cmd])
        + len(payload).to_bytes(2, "little")
        + payload
        + crc16_modbus(payload).to_bytes(2, "little")
    )


def on_notify(sender, data: bytearray):
    print("[NOTIFY]", data.hex(" ").upper())

    with open("notify_log.txt", "a", encoding="utf-8") as f:
        f.write(data.hex(" ").upper() + "\n")


async def find_device():
    devices = await BleakScanner.discover(timeout=7)

    print("[SCAN] devices:")
    for d in devices:
        print(f"  {d.address}  {d.name}")

    for d in devices:
        name = d.name or ""
        if (
            "W" in name
            or "Cyan" in name
            or "Hey" in name
            or "MO" in name
            or "M02" in name
        ):
            return d

    return None


async def main():
    target = await find_device()

    if target is None:
        print("[ERROR] target device not found")
        return

    print(f"[CONNECT] {target.address} {target.name}")

    async with BleakClient(target.address) as client:
        print("[CONNECTED]", client.is_connected)

        await client.start_notify(NOTIFY_UUID, on_notify)
        print("[NOTIFY] subscribed")

        photo_payload = bytes.fromhex("02 01 01")
        photo_packet = build_packet(photo_payload)

        print("[WRITE]", photo_packet.hex(" ").upper())

        # 일부 BLE 기기는 response=False가 더 잘 동작할 수 있음.
        # 먼저 response=False로 테스트하고, 실패하면 True로 변경.
        await client.write_gatt_char(WRITE_UUID, photo_packet, response=False)

        await asyncio.sleep(5)

        await client.stop_notify(NOTIFY_UUID)
        print("[DONE]")


if __name__ == "__main__":
    asyncio.run(main())
```

설치:

```bash
pip install bleak
```

실행:

```bash
python test_photo.py
```

---

## 10. CLI 프로젝트 구조 제안

```text
heycyan-cli/
  pyproject.toml
  README.md

  heycyan/
    __init__.py
    cli.py
    ble.py
    protocol.py
    commands.py
    notify.py
    media.py

  examples/
    test_photo.py
    test_notify_log.py
    test_preview_rtp.py
```

역할:

```text
protocol.py
  - crc16_modbus()
  - build_packet()
  - parse_packet()

commands.py
  - payload 정의
  - photo()
  - video_start()
  - audio_start()
  - preview_start()

ble.py
  - scan()
  - connect()
  - write()
  - subscribe_notify()

notify.py
  - notify 로그 저장
  - ACK/상태 응답 파싱

media.py
  - Wi-Fi UDP/RTP 수신
  - RTP 헤더 제거
  - H264 저장
```

---

## 11. Typer 기반 CLI 예시

```python
import asyncio
import typer
from bleak import BleakClient, BleakScanner

app = typer.Typer()

WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


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
    return (
        bytes([0xBC, cmd])
        + len(payload).to_bytes(2, "little")
        + payload
        + crc16_modbus(payload).to_bytes(2, "little")
    )


COMMANDS = {
    "photo": bytes.fromhex("02 01 01"),
    "video-start": bytes.fromhex("02 01 02"),
    "audio-start": bytes.fromhex("02 01 08"),
    "preview-start": bytes.fromhex("02 01 14 02"),
    "preview-stop": bytes.fromhex("02 01 15 01"),
}


def on_notify(sender, data):
    typer.echo(f"[NOTIFY] {data.hex(' ').upper()}")


async def scan_async():
    devices = await BleakScanner.discover(timeout=7)
    for d in devices:
        typer.echo(f"{d.address}\t{d.name}")


async def send_async(address: str, command: str):
    payload = COMMANDS[command]
    packet = build_packet(payload)

    async with BleakClient(address) as client:
        await client.start_notify(NOTIFY_UUID, on_notify)
        typer.echo(f"[WRITE] {packet.hex(' ').upper()}")

        await client.write_gatt_char(WRITE_UUID, packet, response=False)
        await asyncio.sleep(5)

        await client.stop_notify(NOTIFY_UUID)


@app.command()
def scan():
    asyncio.run(scan_async())


@app.command()
def send(address: str, command: str):
    """
    command:
      photo
      video-start
      audio-start
      preview-start
      preview-stop
    """
    if command not in COMMANDS:
        raise typer.BadParameter(f"unknown command: {command}")

    asyncio.run(send_async(address, command))


if __name__ == "__main__":
    app()
```

설치:

```bash
pip install bleak typer
```

실행:

```bash
python -m heycyan.cli scan
python -m heycyan.cli send AA:BB:CC:DD:EE:FF photo
```

---

## 12. Wi-Fi / RTP 쪽 분석 정리

기존 `wi.txt` 패킷에서 반복되던 형태:

```text
80 23 B9 02
80 23 B9 03
80 23 B9 04
...
```

이 패턴은 RTP 헤더와 잘 맞는다.

RTP 기본 헤더:

```text
Byte 0:
  0x80 = Version 2, no padding, no extension, CSRC count 0

Byte 1:
  payload type

Byte 2-3:
  sequence number

Byte 4-7:
  timestamp

Byte 8-11:
  SSRC
```

따라서 `80 23 B9 02`는 다음처럼 볼 수 있다.

```text
80
  RTP Version 2

23
  Payload Type 후보

B9 02
  Sequence Number 후보
```

개발 방향:

```text
UDP 수신
  ↓
RTP 12바이트 헤더 제거
  ↓
payload 추출
  ↓
H264 NAL 조각 재조립
  ↓
.h264 저장 또는 ffplay 재생
```

초기 테스트용 UDP/RTP 덤프 코드:

```python
import socket

UDP_IP = "0.0.0.0"
UDP_PORT = 56384

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

with open("stream_payload.h264", "wb") as f:
    while True:
        data, addr = sock.recvfrom(2048)

        print(addr, len(data), data[:12].hex(" ").upper())

        # RTP 헤더로 보이면 12바이트 제거
        if len(data) > 12 and data[0] == 0x80:
            payload = data[12:]
        else:
            payload = data

        f.write(payload)
        f.flush()
```

주의:

H264 RTP는 FU-A 조각화가 있을 수 있으므로, 단순 12바이트 제거만으로 바로 재생되지 않을 수 있다.  
그래도 초반에는 파일 저장 후 `ffprobe`, `ffplay`로 확인하는 방식이 빠르다.

---

## 13. 개발 검증 순서

가장 현실적인 순서:

```text
1. BLE scan
2. 장치명 확인
3. BLE connect
4. Notify 구독
5. photo 패킷 전송
6. 안경이 실제 사진을 찍는지 확인
7. notify_log.txt 비교
8. video-start 전송
9. Wi-Fi/RTP 패킷 증가 여부 확인
10. preview-start-ap 전송
11. UDP 포트 수신 테스트
```

첫 성공 기준:

```text
heycyan photo 실행
  ↓
안경에서 촬영음 또는 LED 반응
  ↓
Notify 응답 수신
```

그 다음 목표:

```text
preview-start-ap 실행
  ↓
UDP/RTP 패킷 수신 증가
  ↓
H264 payload 저장
```

---

## 14. 아직 확정 필요 사항

아래는 실제 기기 테스트로 확인해야 한다.

```text
1. write_gatt_char response=True / False 중 어느 쪽이 안정적인지
2. CMD 값이 항상 0x41인지
3. Notify 응답 포맷
4. 영상 종료 payload
5. 음성 종료 payload
6. Wi-Fi AP SSID / 비밀번호 생성 규칙
7. UDP 포트 고정 여부
8. RTP payload type 의미
9. H264 FU-A 조각화 여부
10. 사진 파일 다운로드 API 존재 여부
```

---

## 15. 현재 개발 가능 여부

가능하다.

정확한 표현:

```text
BLE 제어 CLI:
  개발 가능

사진 촬영 명령:
  개발 가능, 실기기 검증 필요

영상/음성 시작:
  개발 가능, 종료 명령은 추가 확인 필요

Notify 응답:
  수신/로그 개발 가능, 의미 파싱은 실측 필요

Wi-Fi 미디어 수신:
  개발 가능, RTP/H264 재조립은 추가 구현 필요

완전 SDK:
  가능하지만 기기 테스트 반복 필요
```

---

## 16. 추천 첫 구현 목표

처음에는 모든 기능을 한 번에 만들지 말고, 아래 하나만 성공시키는 것이 좋다.

```bash
heycyan photo
```

내부 동작:

```text
scan
  ↓
connect
  ↓
notify subscribe
  ↓
BC 41 03 00 02 01 01 10 50 write
  ↓
notify log 저장
  ↓
5초 대기
  ↓
disconnect
```

이게 성공하면 `video-start`, `preview-start-ap`, `UDP/RTP 수신` 순서로 확장한다.

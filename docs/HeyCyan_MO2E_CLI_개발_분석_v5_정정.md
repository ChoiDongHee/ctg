# HeyCyan / MO2E CLI 개발 분석 v5 - 사진 명령 실패 후 정정판

> v4 테스트에서 사진 촬영이 동작하지 않은 이유를 APK 바이트코드 기준으로 재검토한 정정 문서입니다.  
> 핵심 정정: BLE UUID와 패킷 필드 순서가 v4와 다릅니다.

---

## 0. 가장 중요한 정정

v4에서 제안했던 패킷:

```text
BC 41 03 00 02 01 01 10 50
```

은 **순서가 틀렸습니다.**

APK의 실제 `LargeDataHandler.addHeader()` / `BleConsumer.addHeader()` 로직은 다음 순서입니다.

```text
BC [CMD] [LEN_LO] [LEN_HI] [CRC_LO] [CRC_HI] [PAYLOAD...]
```

즉 사진 촬영 명령은 다음이 더 정확한 후보입니다.

```text
BC 41 03 00 10 50 02 01 01
```

---

## 1. v4가 동작하지 않은 이유

v4에서는 포맷을 이렇게 잡았습니다.

```text
BC [CMD] [LEN] [PAYLOAD] [CRC]
```

하지만 APK 실제 `addHeader()`를 보면:

```text
0: BC
1: CMD
2~3: payload length
4~5: CRC16(payload)
6~ : payload
```

입니다.

따라서 payload와 CRC 순서가 바뀌어 있었습니다.

---

## 2. 더 중요한 정정: LargeData용 BLE UUID

HeyCyan APK에는 BLE UUID 계열이 2개 있습니다.

### 일반 명령 UUID

```text
Service
6e40fff0-b5a3-f393-e0a9-e50e24dcca9e

Notify
6e400003-b5a3-f393-e0a9-e50e24dcca9e

Write
6e400002-b5a3-f393-e0a9-e50e24dcca9e
```

### LargeData / Glass Control UUID

APK의 `LargeDataHandler`와 `BleConsumer`가 실제로 사용하는 UUID는 이것입니다.

```text
Service
de5bf728-d711-4e47-af26-65e3012a5dc7

Notify
de5bf729-d711-4e47-af26-65e3012a5dc7

Write
de5bf72a-d711-4e47-af26-65e3012a5dc7
```

사진/영상/미리보기 같은 `glassesControl()` 계열 명령은 **de5bf72a... Write characteristic** 쪽으로 보내야 할 가능성이 높습니다.

즉 v4에서 `6e400002...`에 썼다면 반응이 없을 수 있습니다.

---

## 3. APK 근거

`LargeDataHandler.<clinit>()`에서 확인된 UUID:

```text
SERIAL_PORT_SERVICE
de5bf728-d711-4e47-af26-65e3012a5dc7

SERIAL_PORT_CHARACTER_NOTIFY
de5bf729-d711-4e47-af26-65e3012a5dc7

SERIAL_PORT_CHARACTER_WRITE
de5bf72a-d711-4e47-af26-65e3012a5dc7
```

`LargeDataHandler.getWriteRequest()`:

```text
WriteRequest.getNoRspInstance(
    SERIAL_PORT_SERVICE,
    SERIAL_PORT_CHARACTER_WRITE
)
```

즉 **response=False / write without response** 방식입니다.

---

## 4. addHeader 실제 구조

APK 바이트코드 기준:

```text
new byte[payload.length + 6]

packet[0] = 0xBC
packet[1] = cmd
packet[2:4] = shortToBytes(payload.length)
packet[4:6] = shortToBytes(CRC16(payload))
packet[6:] = payload
```

따라서 Python 빌더는 다음처럼 되어야 합니다.

```python
def build_packet(payload: bytes, cmd: int = 0x41) -> bytes:
    length = len(payload).to_bytes(2, "little")
    crc = crc16_modbus(payload).to_bytes(2, "little")
    return bytes([0xBC, cmd]) + length + crc + payload
```

---

## 5. CRC16

APK `CRC16.calcCrc16()` 기준:

```text
initial: 0xFFFF
polynomial: 0xA001
target: payload only
byte order: little-endian
```

Python:

```python
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
```

---

## 6. 정정된 최종 패킷 후보

| 기능 | Payload | v5 최종 패킷 후보 |
|---|---|---|
| 사진 촬영 | `02 01 01` | `BC 41 03 00 10 50 02 01 01` |
| 영상 시작 | `02 01 02` | `BC 41 03 00 50 51 02 01 02` |
| 음성/녹음 시작 | `02 01 08` | `BC 41 03 00 D0 56 02 01 08` |
| 앨범 가져오기 | `02 01 04 01` | `BC 41 04 00 93 5C 02 01 04 01` |
| AP 앨범 가져오기 | `02 01 04 02` | `BC 41 04 00 D3 5D 02 01 04 02` |
| 실시간 미리보기 AP | `02 01 14 02` | `BC 41 04 00 DE 9D 02 01 14 02` |
| 실시간 미리보기 종료 | `02 01 15 01` | `BC 41 04 00 9F 0C 02 01 15 01` |

---

## 7. 수정된 사진 촬영 테스트 코드

```python
import asyncio
from bleak import BleakClient, BleakScanner

LARGE_SERVICE_UUID = "de5bf728-d711-4e47-af26-65e3012a5dc7"
LARGE_NOTIFY_UUID = "de5bf729-d711-4e47-af26-65e3012a5dc7"
LARGE_WRITE_UUID = "de5bf72a-d711-4e47-af26-65e3012a5dc7"


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
        + crc16_modbus(payload).to_bytes(2, "little")
        + payload
    )


def on_notify(sender, data: bytearray):
    print("[NOTIFY]", data.hex(" ").upper())
    with open("notify_log.txt", "a", encoding="utf-8") as f:
        f.write(data.hex(" ").upper() + "\n")


async def find_device():
    devices = await BleakScanner.discover(timeout=7)

    for d in devices:
        print(d.address, d.name)

    # 이름 조건은 기기마다 다를 수 있으므로 직접 address 지정 방식도 추천
    for d in devices:
        name = d.name or ""
        if "W" in name or "Cyan" in name or "MO" in name or "M02" in name:
            return d

    return None


async def main():
    target = await find_device()
    if not target:
        print("장치를 찾지 못했습니다.")
        return

    async with BleakClient(target.address) as client:
        print("[CONNECTED]", client.is_connected)

        # LargeData notify 구독
        try:
            await client.start_notify(LARGE_NOTIFY_UUID, on_notify)
            print("[NOTIFY] LargeData subscribed")
        except Exception as e:
            print("[WARN] notify subscribe failed:", e)

        # 사진 촬영 payload
        payload = bytes.fromhex("02 01 01")
        packet = build_packet(payload)

        print("[WRITE]", packet.hex(" ").upper())

        # APK는 getNoRspInstance 사용 → response=False
        await client.write_gatt_char(LARGE_WRITE_UUID, packet, response=False)

        await asyncio.sleep(5)

        try:
            await client.stop_notify(LARGE_NOTIFY_UUID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 8. characteristic 확인 코드

먼저 실제 기기에 `de5bf728...` 서비스가 있는지 확인해야 합니다.

```python
import asyncio
from bleak import BleakClient, BleakScanner


async def main():
    devices = await BleakScanner.discover(timeout=7)
    for i, d in enumerate(devices):
        print(i, d.address, d.name)

    idx = int(input("device index: "))
    dev = devices[idx]

    async with BleakClient(dev.address) as client:
        print("[CONNECTED]")

        for service in client.services:
            print("SERVICE", service.uuid)
            for char in service.characteristics:
                print("  CHAR", char.uuid, char.properties)


if __name__ == "__main__":
    asyncio.run(main())
```

확인해야 할 항목:

```text
de5bf728-d711-4e47-af26-65e3012a5dc7
  de5bf729-d711-4e47-af26-65e3012a5dc7 notify
  de5bf72a-d711-4e47-af26-65e3012a5dc7 write / write-without-response
```

이 서비스가 없다면 연결한 BLE 장치가 실제 안경 제어용 GATT가 아닐 수 있습니다.

---

## 9. 다음 테스트 순서

1. `list_services.py`로 `de5bf728...` 서비스가 보이는지 확인
2. 보이면 `de5bf729...` Notify 구독
3. `de5bf72a...` Write에 아래 패킷 전송

```text
BC 41 03 00 10 50 02 01 01
```

4. 안경 LED/촬영음/앱 상태 변화 확인
5. Notify 로그 저장

---

## 10. 그래도 안 되면 확인할 것

### 1) mPackageLength에 따른 분할 문제

APK는 `BleDataBean(packet, mPackageLength)`로 큐에 넣고, `BleConsumer`가 `mPackageLength` 단위로 잘라서 여러 번 write합니다.

사진 패킷은 9바이트라 분할 이슈가 없을 가능성이 높지만, `mPackageLength` 초기화가 필요한 장치일 수도 있습니다.

### 2) initPackageNotify 필요 가능성

앱은 연결 후 패키지 길이 협상 또는 notify 초기화를 먼저 할 수 있습니다.

관련 메서드:

```text
initPackageNotify()
packageLength()
syncDeviceInfo()
syncTime()
```

사진 명령 전에 초기화 명령이 필요한 모델이면, 단독 photo 패킷은 무시될 수 있습니다.

### 3) 일반 UUID와 LargeData UUID 순서

연결 직후 `6e40...` notify와 `de5b...` notify 둘 다 구독해야 할 수 있습니다.

### 4) 현재 연결한 장치가 안경 본체 BLE인지 확인

HeyCyan 계열은 BLE 이름과 Wi-Fi 이름이 다를 수 있습니다.  
또한 폰 앱이 이미 연결 중이면 PC 연결이 실패하거나 명령이 무시될 수 있습니다.

---

## 11. 현재 판단

포기할 단계는 아닙니다.

v4 실패 원인은 꽤 명확합니다.

```text
1. CRC 위치가 틀렸음
2. 사진/영상 제어는 6e40 UUID가 아니라 de5bf72a UUID일 가능성이 큼
3. APK는 write response가 아니라 write without response 사용
```

v5 기준으로 다시 테스트해야 합니다.

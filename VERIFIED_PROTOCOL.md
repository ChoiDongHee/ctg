# HeyCyan M02C 검증된 프로토콜

> 실기기(M02C_EB50) 테스트로 완전 확인된 내용

---

## BLE UUID (확정)

| UUID | 역할 |
|------|------|
| `de5bf72a-d711-4e47-af26-65e3012a5dc7` | **Write** (명령 전송) |
| `de5bf729-d711-4e47-af26-65e3012a5dc7` | **Notify** (응답 수신) |

---

## 패킷 포맷 (v5 확정)

```
BC [CMD=0x41] [LEN_LO] [LEN_HI] [CRC16_LO] [CRC16_HI] [PAYLOAD...]
```

- CRC16 Modbus (init=0xFFFF, poly=0xA001, LE output)
- `response=False` (write without response)

---

## 명령 패킷 (확인됨)

| 기능 | Payload | 전체 패킷 |
|------|---------|----------|
| 사진 촬영 ✅ | `02 01 01` | `BC 41 03 00 10 50 02 01 01` |
| 영상 시작 | `02 01 02` | `BC 41 03 00 50 51 02 01 02` |
| 영상 중지 | `02 01 03` | `BC 41 03 00 91 91 02 01 03` |
| 음성 시작 | `02 01 08` | `BC 41 03 00 D0 56 02 01 08` |
| 음성 중지 | `02 01 0C` | `BC 41 03 00 51 97 02 01 0C` |
| Wi-Fi AP ✅ | `02 01 04 02` | `BC 41 04 00 D3 5D 02 01 04 02` |
| 앨범 개수 ✅ | `02 04` | `BC 41 02 00 01 13 02 04` |

---

## Wi-Fi 파일 전송 (확정)

1. AP 명령 전송 → BLE Notify로 SSID/PW 수신
2. SSID: `M02C_[MAC주소]` (예: `M02C_3CA6DE5BEB50`)
3. 비밀번호: `123456789`
4. 안경 HTTP 서버 IP: **`192.168.31.1`**
5. 파일 목록: `GET http://192.168.31.1/files/media.config`
6. 파일 다운로드: `GET http://192.168.31.1/files/[filename]`

---

## BLE 응답 파싱

```python
# v5 응답 포맷: BC [CMD] [LEN] [CRC] [PAYLOAD]
def parse_response(data):
    cmd = data[1]
    length = int.from_bytes(data[2:4], "little")
    payload = data[6:6+length]
    return cmd, payload

# Wi-Fi SSID/PW 파싱
# payload = [cmd_echo 4B] [SSID_LEN 2B LE] [PW_LEN 2B LE] [SSID] [PW]
idx = 4
ssid_len = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
pw_len   = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
ssid = payload[idx:idx+ssid_len].decode("ascii")
pw   = payload[idx+ssid_len:idx+ssid_len+pw_len].decode("ascii")

# 배터리: CMD=0x73 payload[0]=0x05 → payload[1]=배터리%, payload[2]=충전
```

---

## 실행 방법

```bash
# 전체 테스트 (사진+음성+영상 촬영 → Wi-Fi 다운로드)
python test_capture_all.py

# 사진만
python test_v5_photo.py

# Wi-Fi 다운로드만
python test_wifi_full.py
```

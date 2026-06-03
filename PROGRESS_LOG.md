# HeyCyan M02C 개발 진행 로그

## 기기 정보
| 항목 | 값 |
|------|-----|
| 모델 | PEJE M08C / M02C_EB50 |
| BLE 주소 | `3C:A6:DE:5B:EB:50` |
| MTU | 517 |
| 배터리 | 26% (2026-06-02 기준, 충전 필요) |

---

## BLE 서비스 전체 맵 (nRF Connect 확인)

```
Generic Access (0x1800)
  └─ Device Name [R W] (0x2A00)

Unknown Service (0xAE30)
  ├─ ae01 [W WNR] — Write 채널 (aa55 명령 → ae02 에코)
  ├─ ae02 [N]     — ae01 명령 에코 수신
  ├─ ae03 [WNR]   — Write 채널 2
  ├─ ae04 [N]     — 알 수 없는 Notify
  ├─ ae05 [I]     — Indicate
  └─ ae10 [R W]   — Read/Write

Unknown Service (0x3802)
  └─ 4a02 [N R W]

Unknown Service (0xAE3A)
  ├─ ae3b [WNR]
  └─ ae3c [N]

Generic Attribute (0x1801)
  └─ 2a05 [I] — Service Changed

Nordic UART (6e40fff0-...)
  ├─ RX (6e400002) [W WNR] — Write
  └─ TX (6e400003) [N]     — Notify

Serial Port (de5bf728-...)
  ├─ de5bf72a [W WNR] — Write (SERIAL_PORT_CHARACTER_WRITE)
  └─ de5bf729 [N]     — Notify (bc73 데이터)

Device Information (0x180A)
  ├─ 0x2A25 [R] — Serial Number
  ├─ 0x2A27 [R] — Hardware Revision
  ├─ 0x2A26 [R] — Firmware Revision
  └─ 0x2A23 [R] — System ID

Unknown Service (0xFEE1)
  └─ fee3 [N R W]
```

---

## bc73 프로토콜 (de5bf729 Notify)

### 형식
```
[BC] [73] [type] [sub] [data...] [checksum?]
```

### type=02: Heartbeat (매 1~2초 자동)
```
BC 73 02 00 86 96 0b 36
BC 73 02 00 c6 97 0b 35
```
→ 센서 데이터 (가속도계/자이로스코프 추정)

### type=03: 상태 업데이트 (매 10초 자동, nRF Connect에서 확인)
```
BC-73-03-00-6A-A1-05-1A-00  배터리 26%
BC-73-03-00-6A-51-05-19-00  배터리 25%
BC-73-03-00-6B-C1-05-18-00  배터리 24%
BC-73-03-00-6E-31-05-17-00  배터리 23%
```

**배터리 파싱 (Android MainActivity.kt 코드 기반):**
```python
if data[0]==0xBC and data[1]==0x73 and data[2]==0x03:
    for i in range(4, len(data)-2):
        if data[i] == 0x05:          # 배터리 마커
            battery = data[i+1]      # 0-100%
            charging = data[i+2]     # 0=미충전, 1=충전중
            break
```

---

## 테스트 결과 요약

| 채널 | 형식 | 명령 | 결과 |
|------|------|------|------|
| ae01 | aa55 | 배터리(0x20) | ae02 에코 수신 ✅ |
| ae01 | aa55 | 사진(0x01/0x02) | ae02 에코만, 촬영 안 됨 ❌ |
| ae01 | aa55 | Wi-Fi(0x40) | ae02 에코만, SSID 미생성 ❌ |
| de5bf72a | raw [0x02,0x01,0x01] | 사진 | heartbeat만 ❌ |
| de5bf72a | bc73 프레임 | 사진/Wi-Fi | heartbeat만 ❌ |
| 6e400002 | raw [0x02,0x01,0x01] | 사진 | heartbeat만 ❌ |

**주요 발견:**
- type=03 배터리 알림은 **9개 채널 모두 구독** 시에만 수신 가능
- 우리 테스트는 2-3개만 구독 → type=03 미수신
- nRF Connect(Bonded) 구독 채널: ae02, ae04, ae05, 4a02, ae3c, 2a05, UART_TX, de5bf729, fee3

---

## AAR 디컴파일 분석 결과

### 주요 상수 (Constants.class)
```
UUID_SERVICE    = 6e40fff0-... (Nordic UART Service)
UUID_WRITE      = 6e400002-... (Nordic UART RX)
UUID_READ       = 6e400003-... (Nordic UART TX)
SERIAL_PORT_SERVICE            = de5bf728-...
SERIAL_PORT_CHARACTER_WRITE    = de5bf72a-...
SERIAL_PORT_CHARACTER_NOTIFY   = de5bf729-...
CMD_TAKING_PICTURE             = 0x02
```

### 명령 형식 (BaseReqCmd.addCRC)
```python
# CRC = sum(all_bytes_except_last) & 0xFF (마지막 바이트에 저장)
```

### Android 샘플 명령 (MainActivity.kt)
```kotlin
// 사진: LargeDataHandler.getInstance().glassesControl(byteArrayOf(0x02, 0x01, 0x01))
// 영상: byteArrayOf(0x02, 0x01, 0x02/0x03)
// Wi-Fi: byteArrayOf(0x02, 0x01, 0x04)
// 미디어수: byteArrayOf(0x02, 0x04)
// 오디오: byteArrayOf(0x02, 0x01, 0x08/0x0C)
```

---

## Wi-Fi 파일 전송

### 확인된 정보
- **비밀번호**: `123456789` (iOS GlassesWiFiHandler.m 고정값)
- **IP 범위**: `192.168.49.x` (WiFi Direct P2P)
- **HTTP API**:
  - `GET http://[IP]/files/media.config` → 파일 목록
  - `GET http://[IP]/files/[filename]` → 파일 다운로드

---

## 미해결 과제

1. **명령 전송 채널 미확정**: aa55/ae01, bc73/de5b, raw/UART 모두 시도했으나 동작 안 함
2. **type=03 PC 수신 미확인**: nRF Connect(Android)에서는 수신, Python(Windows)에서는 미확인
3. **Wi-Fi 핫스팟 미활성화**: 0x40/[0x02,0x01,0x04] 명령 후 SSID 미생성

## 다음 할 일 (충전 후)

1. `test_paired.py` 실행 → 9채널 구독 후 type=03 수신 확인
2. type=03 수신되면 → 사진 명령 [0x02,0x01,0x01] 전송 테스트
3. Wi-Fi 핫스팟 활성화 → SSID 123456789 연결 → HTTP 파일 다운로드
4. Android HCI 스누프 로그 (선택사항 — 위 3개 실패 시)

---

*최종 업데이트: 2026-06-03*

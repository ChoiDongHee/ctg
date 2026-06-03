# HeyCyan M02C BLE 프로토콜 리서치 노트

## 기기 정보
- 모델: PEJE M08C / M02C_EB50
- BLE 주소: `3C:A6:DE:5B:EB:50`
- MTU: 517

---

## 확인된 BLE 서비스 목록

| 서비스 UUID | 특성 | 속성 | 역할 |
|-------------|------|------|------|
| `0000ae30-...` | `0000ae01-...` | write-without-response | ??? |
| `0000ae30-...` | `0000ae02-...` | notify | ae01 echo 채널 |
| `de5bf728-...` | `de5bf72a-...` | write | SERIAL_PORT_CHARACTER_WRITE |
| `de5bf728-...` | `de5bf729-...` | notify | SERIAL_PORT_CHARACTER_NOTIFY (bc73 heartbeat) |
| `6e40fff0-...` | `6e400002-...` | write | Nordic UART RX |
| `6e40fff0-...` | `6e400003-...` | notify | Nordic UART TX |

---

## 현재까지 테스트 결과

### ae01 채널 (aa55 형식)
| 명령 | 전송 데이터 | ae02 응답 | de5b 응답 | 동작여부 |
|------|-----------|----------|----------|---------|
| 배터리(0x20) | `aa55200020` | `aa55200020` (echo) | heartbeat | ❓ |
| 사진(0x01) | `aa55010001` | `aa55010001` (echo) | heartbeat | ❌ 사진 안 찍힘 |
| 사진(0x02) | `aa55020002` | `aa55020002` (echo) | heartbeat | ❌ |
| Wi-Fi(0x40) | `aa55400040` | `aa55400040` (echo) | heartbeat | ❌ SSID 미생성 |

### de5b 채널 직접 write
| 형식 | 전송 데이터 | 응답 | 동작여부 |
|------|-----------|------|---------|
| raw bytes | `02 01 01` | heartbeat only | ❌ |
| bc73 frame | `BC 73 02 01 01 33` | heartbeat only | ❌ |
| bc73 frame | `BC 73 02 01 04 36` | heartbeat only | ❌ |

### de5b Notify (bc73 형식) — 수신 데이터
```
bc73 02 00 86 96 0b 36  (주기적 heartbeat, type=02)
bc73 02 00 c6 97 0b 35  (주기적 heartbeat, type=02)
```

**배터리 데이터 확인**: `bc73 03 00 76 61 05 36 00`
- loadData[6] = `0x05` → 배터리 타입
- loadData[7] = `0x36` = 54 → **배터리 54%**
- loadData[8] = `0x00` → 충전 안 됨

---

## AAR 디컴파일 분석 결과

### 중요 상수 (Constants.class)
```
UUID_SERVICE = 6e40fff0-...  (Nordic UART Service)
UUID_WRITE   = 6e400002-...  (Nordic UART RX)
UUID_READ    = 6e400003-...  (Nordic UART TX)

SERIAL_PORT_SERVICE            = de5bf728-...
SERIAL_PORT_CHARACTER_WRITE    = de5bf72a-...
SERIAL_PORT_CHARACTER_NOTIFY   = de5bf729-...

CMD_TAKING_PICTURE = 0x02  ← (상수풀에서 확인)
```

### 명령 형식 (BaseReqCmd + addCRC)
```python
# addCRC: 마지막 바이트에 합산 체크섬
def add_crc(data: bytearray):
    data[-1] = sum(data[:-1]) & 0xFF
```

### 의심 명령 형식
1. **LargeDataHandler.glassesControl([0x02, 0x01, 0x01])** → de5b 채널로 전송
   - 실제 BLE 바이트: `BC 73 02 01 01 [CRC=33]` 예상
   - BUT: 테스트에서 동작 안 함

2. **CommandHandle** → Nordic UART(6e400002) 채널 사용
   - BaseReqCmd 구조: `[key][type][subData][CRC]`

---

## 미해결 문제

1. **ae01/ae02 에코만 옴**: 명령을 인식하지만 실행 안 함
2. **de5b write 무응답**: bc73 형식으로 써도 type=03 응답 없음
3. **Wi-Fi 핫스팟 미생성**: 0x40 명령 후 SSID 나타나지 않음

---

## 다음 단계: Android HCI 스누프 로그

가장 확실한 방법:
```
① Android 개발자 옵션 → "블루투스 HCI 스누프 로그 사용" ON
② HeyCyan 앱 → 안경 연결 → 사진 촬영 → 파일 전송
③ adb pull /data/misc/bluetooth/logs/btsnoop_hci.log
④ Wireshark로 분석
   필터: btle.data.handle == [de5b write handle]
```

또는 nRF Connect 앱으로 실시간 BLE 패킷 모니터링.

---

## Wi-Fi 비밀번호 (iOS 소스 확인)
```
비밀번호: 123456789 (GlassesWiFiHandler.m에서 고정값으로 사용)
IP 범위:  192.168.49.x (WiFi Direct P2P 네트워크)
HTTP API: http://[IP]/files/media.config
          http://[IP]/files/[filename]
```

---

*2026-06-02 작성*

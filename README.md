# HeyCyan M02C — PC 관리 대시보드

> **기기**: PEJE M08C / M02C_EB50 (`3C:A6:DE:5B:EB:50`)  
> **스택**: Python 3.11+ · FastAPI · bleak 0.22.3 · WebSocket  
> **상태**: ✅ 사진·영상·음성 촬영 + Wi-Fi 파일 전송 완전 동작 확인  
> **개발**: [ChoiDongHee](https://github.com/ChoiDongHee) · Claude Sonnet 4.6

---

## 빠른 시작

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 전체 테스트 (사진+음성+영상 촬영 → Wi-Fi 다운로드)
python test_capture_all.py

# 3. 웹 관리자 실행
python main.py
# → http://localhost:8000
```

---

## 검증된 기능

| 기능 | 상태 | 비고 |
|------|------|------|
| BLE 자동 연결 | ✅ | M0* 이름 자동 탐색 |
| 사진 촬영 | ✅ | 앨범 개수 증가 확인 |
| 영상 녹화 시작/중지 | ✅ | |
| 음성 녹음 시작/중지 | ✅ | OPUS 포맷 |
| Wi-Fi 핫스팟 활성화 | ✅ | SSID/PW BLE 수신 |
| 파일 다운로드 | ✅ | 사진·영상·음성 자동 분류 |
| 배터리 모니터링 | ✅ | BLE Notify bc73 파싱 |

---

## 확정 프로토콜

### BLE UUID

| UUID | 속성 | 용도 |
|------|------|------|
| `de5bf72a-d711-4e47-af26-65e3012a5dc7` | Write | 명령 전송 |
| `de5bf729-d711-4e47-af26-65e3012a5dc7` | Notify | 응답 수신 |

### 패킷 포맷 (v5)

```
BC 41 [LEN_LO] [LEN_HI] [CRC16_LO] [CRC16_HI] [PAYLOAD...]
```

- CRC16 Modbus (init=0xFFFF, poly=0xA001, LE)
- `response=False` (write without response)

### 명령 패킷

| 기능 | 전체 패킷 |
|------|----------|
| 사진 촬영 | `BC 41 03 00 10 50 02 01 01` |
| 영상 시작 | `BC 41 03 00 50 51 02 01 02` |
| 영상 중지 | `BC 41 03 00 91 91 02 01 03` |
| 음성 시작 | `BC 41 03 00 D0 56 02 01 08` |
| 음성 중지 | `BC 41 03 00 51 97 02 01 0C` |
| Wi-Fi AP  | `BC 41 04 00 D3 5D 02 01 04 02` |
| 앨범 개수 | `BC 41 02 00 01 13 02 04` |

---

## Wi-Fi 파일 전송 플로우

```
① BLE: AP 명령 전송 (02 01 04 02)
        ↓
② BLE Notify: SSID + 비밀번호 수신
   SSID = M02C_[MAC주소]   예) M02C_3CA6DE5BEB50
   PW   = 123456789
        ↓
③ Windows Wi-Fi → 안경 SSID 연결
        ↓
④ HTTP: 192.168.31.1/files/media.config → 파일 목록
        ↓
⑤ 병렬 다운로드 → downloads/photo|video|audio/
```

---

## 아키텍처

```
브라우저 (localhost:8000)
    │
    ├── WebSocket /ws        실시간 상태 (배터리, 알림)
    └── REST API
         ├── /api/ble/*      BLE 스캔·연결·촬영 제어
         ├── /api/wifi/*     Wi-Fi 활성화·파일 싱크
         └── /api/media/*    다운로드 파일 목록

FastAPI 서버 (main.py)
    ├── _auto_connect_loop() M02C 자동 연결
    ├── _keepalive_loop()    15초마다 배터리 체크
    └── core/
        ├── ble_manager.py   v5 패킷·bc73 파싱
        ├── wifi_transfer.py HTTP 파일 다운로드
        ├── audio_pipeline.py OPUS→WAV 변환
        └── transcription.py Whisper STT
```

---

## 파일 구조

```
glasses_admin/
├── test_capture_all.py   ← 메인 테스트 (사진+음성+영상)
├── test_v5_photo.py      ← 사진 단독 테스트
├── test_wifi_full.py     ← Wi-Fi 다운로드 테스트
├── main.py               ← FastAPI 서버
├── requirements.txt
├── core/
│   ├── ble_manager.py    ← v5 프로토콜 구현
│   ├── wifi_transfer.py
│   ├── audio_pipeline.py
│   └── transcription.py
├── static/               ← 관리자 UI
├── downloads/            ← 다운로드 파일
│   ├── photo/
│   ├── video/
│   └── audio/
├── docs/                 ← 분석 문서
├── logs/                 ← nRF Connect 로그
├── VERIFIED_PROTOCOL.md  ← 확정 프로토콜 정리
└── tests/archive/        ← 구 탐색 테스트 (참고용)
```

---

## bc73 응답 파싱

```python
# 배터리: payload[0]=0x05 → [1]=배터리%, [2]=충전상태
if payload[0] == 0x05:
    battery = payload[1]   # 0~100%
    charging = payload[2]  # 0 or 1

# Wi-Fi SSID/PW: payload = [cmd_echo 4B] [SSID_LEN 2B] [PW_LEN 2B] [SSID] [PW]
idx = 4
ssid_len = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
pw_len   = int.from_bytes(payload[idx:idx+2], "little"); idx += 2
ssid = payload[idx:idx+ssid_len].decode("ascii")
pw   = payload[idx+ssid_len:idx+ssid_len+pw_len].decode("ascii")
```

---

## 기기 제약

- 배터리 **15% 미만** → 촬영·전송 불가
- 영상 녹화 + 음성 녹음 **동시 불가**
- BLE **1기기만** 연결 가능 (폰 연결 시 PC 불가)
- 오디오: raw OPUS 패킷 → Ogg 래핑 후 재생 가능

---

## 참고

| 레포 | 설명 |
|------|------|
| [ebowwa/HeyCyanSmartGlassesSDK](https://github.com/ebowwa/HeyCyanSmartGlassesSDK) | 공식 iOS/Android SDK |
| [FerSaiyan/Alternative-HeyCyan-App-and-SDK](https://github.com/FerSaiyan/Alternative-HeyCyan-App-and-SDK) | CyanBridge v2.0 |

---

*Made by [ChoiDongHee](https://github.com/ChoiDongHee) · Powered by [Claude Sonnet 4.6](https://claude.ai)*

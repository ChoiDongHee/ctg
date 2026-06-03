# HeyCyan Smart Glasses — PC Admin Dashboard

> **기기**: PEJE M08C / M02C_EB50  
> **스택**: Python 3.11+ / FastAPI / bleak / WebSocket  
> **상태**: BLE 연결 완료 · 배터리 모니터링 완료 · 사진/Wi-Fi 명령 테스트 중

---

## 목차

1. [시작하기](#시작하기)
2. [아키텍처](#아키텍처)
3. [BLE 프로토콜](#ble-프로토콜)
4. [워크플로우](#워크플로우)
5. [API 레퍼런스](#api-레퍼런스)
6. [남은 할일](#남은-할일)

---

## 시작하기

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 서버 실행
python main.py

# 3. 브라우저
http://localhost:8000
```

**Windows 주의**: Python 3.13 + bleak 0.22.3 조합 필요 (bleak 3.x 비권장)

---

## 아키텍처

```
브라우저 (localhost:8000)
    │
    ├── WebSocket /ws        ← 실시간 상태 (배터리, 알림, 로그)
    └── REST API
         ├── /api/ble/*      BLE 스캔/연결/기기 제어
         ├── /api/wifi/*     Wi-Fi 핫스팟 · 파일 싱크
         ├── /api/media/*    다운로드 파일 목록
         └── /api/audio/*    OPUS→WAV 변환 · STT

FastAPI 서버 (main.py)
    ├── _auto_connect_loop()   서버 시작 시 M02C 자동 연결
    ├── _keepalive_loop()      15초마다 배터리 체크 + 절전 방지
    ├── core/ble_manager.py    BLE 연결 · bc73 파싱
    ├── core/wifi_transfer.py  HTTP 파일 다운로드
    ├── core/audio_pipeline.py OPUS(40byte) → WAV 변환
    └── core/transcription.py  Whisper STT
```

---

## BLE 프로토콜

### 확인된 UUID

| 상수명 | UUID | 속성 |
|--------|------|------|
| `WRITE_CHAR` | `de5bf72a-d711-4e47-af26-65e3012a5dc7` | Write |
| `NOTIFY_CHAR` | `de5bf729-d711-4e47-af26-65e3012a5dc7` | Notify |
| `UART_WRITE` | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | Write |
| `UART_READ` | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | Notify |
| `AE01_WRITE` | `0000ae01-0000-1000-8000-00805f9b34fb` | Write |
| `AE02_NOTIFY` | `0000ae02-0000-1000-8000-00805f9b34fb` | Notify |

### bc73 응답 형식 (de5bf729 Notify)

```
[BC 73] [type] [sub] [data...]

type=02  Heartbeat (1~2초 간격, 센서 데이터)
type=03  상태 업데이트 (10초 간격, 배터리 포함)
         → BC-73-03-00-XX-XX-05-[배터리%]-[충전:0/1]
```

**배터리 파싱:**
```python
if data[0]==0xBC and data[1]==0x73 and data[2]==0x03:
    for i in range(4, len(data)-2):
        if data[i] == 0x05:
            battery = data[i+1]   # 0~100 %
            charging = data[i+2]  # 0=미충전, 1=충전중
```

### Android SDK 명령 (LargeDataHandler.glassesControl)

```python
take_photo    = bytes([0x02, 0x01, 0x01])
start_video   = bytes([0x02, 0x01, 0x02])
stop_video    = bytes([0x02, 0x01, 0x03])
wifi_transfer = bytes([0x02, 0x01, 0x04])
media_count   = bytes([0x02, 0x04])
start_audio   = bytes([0x02, 0x01, 0x08])
stop_audio    = bytes([0x02, 0x01, 0x0C])
ai_photo      = bytes([0x02, 0x01, 0x06, 0x02, 0x02, 0x02])
```

---

## 워크플로우

### 1. 전체 흐름

```
안경 켜기
    │
    ▼
서버 자동 스캔 (M0* 이름 패턴)
    │
    ▼
BLE 연결 + 페어링 (client.pair())
    │
    ▼
9개 Notify 채널 구독 (nRF Connect 방식)
    │
    ├──── bc73 type=03 수신 → 배터리% 표시
    │
    ▼
사진/영상/오디오 제어 버튼
    │
    ▼
Wi-Fi 핫스팟 활성화 명령
    │
    ▼
Windows Wi-Fi → 안경 SSID 연결 (비밀번호: 123456789)
    │
    ▼
HTTP GET /files/media.config
    │
    ▼
병렬 파일 다운로드 (사진/영상/오디오)
    │
    ▼
관리자 페이지 파일 브라우저 표시
    │
    └──── 오디오: OPUS→WAV 변환 → Whisper STT
```

### 2. BLE 연결 절차 (상세)

```
① BleakScanner.discover(timeout=15s) — M0* 이름 탐색
② BleakScanner.find_device_by_address(addr, timeout=10s)
③ BleakClient.connect(timeout=15s)
④ client.pair() — BONDED 연결 (type=03 수신 조건)
⑤ 9채널 동시 Notify 구독
⑥ get_battery() + get_media_count()
```

### 3. 파일 전송 절차

```
① BLE: enable_wifi_transfer() → [0x02,0x01,0x04] 전송
② 안경: Wi-Fi 핫스팟 생성
③ PC: netsh wlan connect [SSID] (비밀번호: 123456789)
④ HTTP: GET http://192.168.49.x/files/media.config
⑤ 병렬 다운로드: GET http://[IP]/files/[filename]
⑥ OPUS 파일: wrap_opus_in_ogg() → ffmpeg → WAV
```

---

## API 레퍼런스

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/status` | 전체 상태 (BLE, 배터리, 미디어수) |
| POST | `/api/ble/scan` | BLE 스캔 |
| POST | `/api/ble/connect/{addr}` | 연결 |
| POST | `/api/device/photo` | 사진 촬영 |
| POST | `/api/device/video/start` | 영상 시작 |
| POST | `/api/device/video/stop` | 영상 중지 |
| POST | `/api/device/audio/start` | 오디오 시작 |
| POST | `/api/device/audio/stop` | 오디오 중지 |
| POST | `/api/wifi/enable` | Wi-Fi 핫스팟 활성화 |
| POST | `/api/wifi/sync` | 파일 전체 싱크 |
| GET | `/api/media/list` | 다운로드된 파일 목록 |
| POST | `/api/audio/convert/{file}` | OPUS → WAV 변환 |
| POST | `/api/audio/transcribe/{file}` | Whisper STT |
| WS | `/ws` | 실시간 WebSocket |

---

## 기기 제약

- 배터리 **15% 미만** → 영상/전송 불가
- **영상 + 오디오** 동시 녹화 불가
- **BLE 1개 기기만** 연결 (폰 연결 시 PC 불가)
- Wi-Fi 전송 중 BLE 명령 일시 중단
- 오디오: raw OPUS 패킷(40byte 고정) → Ogg 래핑 필요

---

## 폴더 구조

```
glasses_admin/
├── main.py                  FastAPI 서버 진입점
├── requirements.txt         패키지 목록
├── known_device.txt         저장된 안경 BLE 주소
├── core/
│   ├── ble_manager.py       BLE 연결 + bc73 파싱
│   ├── wifi_transfer.py     Wi-Fi HTTP 파일 전송
│   ├── audio_pipeline.py    OPUS→WAV + 오디오 처리
│   └── transcription.py     Whisper STT (로컬/클라우드)
├── static/
│   ├── index.html           관리자 대시보드
│   ├── css/style.css
│   └── js/app.js
├── downloads/               다운로드된 미디어 파일
├── test_paired.py           메인 테스트 스크립트
├── README.md                이 파일
├── TODO.md                  남은 할일
├── PROGRESS_LOG.md          BLE 프로토콜 연구 기록
└── LOG_ANALYSIS.md          nRF Connect 로그 분석
```

---

## 참고 레포지토리

| 레포 | 설명 |
|------|------|
| [ebowwa/HeyCyanSmartGlassesSDK](https://github.com/ebowwa/HeyCyanSmartGlassesSDK) | 공식 iOS/Android SDK |
| [FerSaiyan/Alternative-HeyCyan-App-and-SDK](https://github.com/FerSaiyan/Alternative-HeyCyan-App-and-SDK) | CyanBridge v2.0 |
| [hbldh/bleak](https://github.com/hbldh/bleak) | Python BLE 라이브러리 |
| [openai/whisper](https://github.com/openai/whisper) | 로컬 STT |

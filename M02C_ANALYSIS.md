# M02C 스마트 안경 프로토콜 분석

## 기기 정보

| 항목 | 값 |
|------|-----|
| 모델 | M02C_EB50 (HeyCyan 안경 PC) |
| BLE MAC | `3C:A6:DE:5B:EB:50` |
| WiFi SSID | `WF1_686725cd4952` |
| WiFi 비밀번호 | `123456789` |
| 안경 HTTP IP | `192.168.49.64` (WiFi Direct P2P, PCAPdroid 확인) |
| HTTP 포트 | 80 |

---

## BLE GATT 프로토콜

### 구독 채널 (9개)

| UUID (축약) | 역할 |
|------------|------|
| `ae02` | 명령 쓰기 (WRITE) |
| `ae04` | 읽기 응답 |
| `ae05` | 보조 채널 |
| `4a02` | 보조 채널 |
| `ae3c` | 보조 채널 |
| `UART_READ (6e40...03)` | UART 수신 |
| `de5bf729` | **BC-73 배터리 자동 알림** |
| `fee3` | 보조 채널 |
| `2a05` | 간격 변경 (Preferred Connection Parameters) |

### BC-73 프레임 (배터리 자동 송신)

```
BC 73 [type] [len] [crc1] [crc2] [payload...]
```

- `type = 0x03`: 배터리 상태
- payload에서 `data[i] == 0x05` → `data[i+1]` = 배터리%, `data[i+2]` = 충전 여부
- **자동 송신**: 10~15초 주기, `de5bf729` notify 채널로 수신

### BC-02 프레임 (ACK)

```
BC 02 [cmd] [00] [crc1] [crc2]
```

명령 전송 후 안경이 BC-02로 응답.

### AA55 에코 프레임

```
AA 55 [len] [crc] [payload]
```

명령 전송 시 에코로 돌아옴.

---

## 명령 테이블

| 명령 | BLE 페이로드 | 응답 |
|------|------------|------|
| 사진 촬영 | `BC 02 01 00 .. BC 73 wrapping 0x02 0x01 0x01` | BC-02 ACK |
| 영상 녹화 | `BC 73 wrapping 0x02 0x01 0x02` | BC-02 ACK |
| 사운드 녹음 | `BC 73 wrapping 0x02 0x01 0x03` | BC-02 ACK |
| WiFi 전송 활성화 | `BC 73 wrapping 0x02 0x01 0x04` + `AA55 0x40` | BC-02 ACK |
| 배터리 조회 | `AA 55 20 00 20` | AA55 에코 (BC-73 자동 수신) |

**`_cmd(type, subtype, action)`** 내부 구현:
```python
inner = bytes([type, subtype, action])
crc   = sum(inner) & 0xFF
frame = b'\xBC\x73' + bytes([len(inner)]) + bytes([crc]) + inner
```

---

## WiFi 다운로드 프로토콜

### PCAPdroid 캡처 분석 (2026-06-05)

안드로이드 앱이 안경 WiFi에 연결 후 HTTP로 파일 다운로드:

| 연결 # | 포트 | 송신 | 수신 | 내용 |
|--------|------|------|------|------|
| #1 | 39488 | 350B | 391B | `GET /files/media.config` → 파일 목록 |
| #2 | 39504 | 9.9KB | ~1.0MB | 사진/영상 다운로드 (~977KB) |
| #3 | 39516 | 36KB | ~5.1MB | 사진/영상/사운드 다운로드 (~4.8MB) |
| #4 | 39522 | 400B | 3.7KB | 완료 확인 |

- 총 전송: **5.8MB / 3.67초**
- 모두 `192.168.49.64:80` (HTTP, not HTTPS)

### HTTP 엔드포인트

```
GET http://192.168.49.64/files/media.config
→ 응답 (plaintext, 한 줄에 파일명):
IMG_20260605_173145.jpg
VID_20260605_173120.mp4
REC_20260605_173100.opus
```

```
GET http://192.168.49.64/files/<filename>
→ 바이너리 파일 응답
```

### 미디어 타입 분류

| 확장자 | 타입 | 저장 폴더 |
|--------|------|---------|
| `.jpg` `.jpeg` `.png` | photo | `downloads/photo/` |
| `.mp4` `.mov` | video | `downloads/video/` |
| `.opus` `.wav` `.ogg` | audio | `downloads/audio/` |

---

## Windows PC 연결 방법

### WiFi XML 프로필 (netsh)

핵심: `AES` (CCMP 아님), `useOneX=false`, `autoSwitch=false`, **BOM 없이 UTF-8 저장**

```xml
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>WF1_686725cd4952</name>
    <SSIDConfig>
        <SSID>
            <hex>574631363836373235636434393532</hex>
            <name>WF1_686725cd4952</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <autoSwitch>false</autoSwitch>
    <MSM><security>
        <authEncryption>
            <authentication>WPA2PSK</authentication>
            <encryption>AES</encryption>
            <useOneX>false</useOneX>
        </authEncryption>
        <sharedKey>
            <keyType>passPhrase</keyType>
            <protected>false</protected>
            <keyMaterial>123456789</keyMaterial>
        </sharedKey>
    </security></MSM>
</WLANProfile>
```

```powershell
netsh wlan delete profile name=WF1_686725cd4952
netsh wlan add profile filename=glasses.xml user=current
netsh wlan connect name=WF1_686725cd4952
```

---

## 전체 플로우

```
1. BLE 연결 (3C:A6:DE:5B:EB:50)
   └── 9채널 구독 자동 완료
   └── BC-73 배터리 10~15초 주기 자동 수신

2. 사진/영상/사운드 촬영
   └── take_photo()    → _cmd(0x02, 0x01, 0x01)
   └── take_video()    → _cmd(0x02, 0x01, 0x02)
   └── take_audio()    → _cmd(0x02, 0x01, 0x03)
   └── BC-02 ACK 수신

3. WiFi 전송 모드 활성화
   └── enable_wifi_transfer() → _cmd(0x02, 0x01, 0x04)
   └── SSID WF1_686725cd4952 브로드캐스트 시작 (5~15초 내)

4. PC WiFi 연결 (WF1_686725cd4952, pw: 123456789)
   └── PC IP: 192.168.49.x 대역 할당
   └── 안경 IP: 192.168.49.64

5. HTTP 파일 다운로드
   └── GET /files/media.config → 파일 목록
   └── GET /files/<photo.jpg>  → 사진 (~1MB)
   └── GET /files/<video.mp4>  → 영상 (~5MB)
   └── GET /files/<rec.opus>   → 사운드
   └── 총 3.67초 완료
```

---

## 테스트 파일

| 파일 | 목적 |
|------|------|
| `test_photo_wifi.py` | BLE→사진→WiFi→다운로드 통합 테스트 |
| `test_full_flow.py` | 배터리 포함 전체 플로우 |
| `test_wifi_connect.py` | WiFi 연결만 테스트 |
| `test_wifi_download.py` | 목업 HTTP 서버로 다운로드 테스트 |
| `test_ble_wifi_info.py` | BLE WiFi 정보 추출 |

---

## 알려진 이슈

| 증상 | 원인 | 해결 |
|------|------|------|
| BC-73 배터리 미수신 | 안경 절전 상태 | 안경 재시작 후 연결 |
| WiFi SSID 미감지 | WiFi 활성화 지연 (5~30초) | 30초 폴링 대기 |
| PC IP=192.168.0.3 | axuser 공유기가 먼저 DHCP 응답 | `netsh wlan disconnect` 후 전용 연결 |
| netsh 오류 0x80001 | XML에 `CCMP` 또는 BOM | `AES` + `useOneX=false` + BOM 없이 저장 |

---

## 참고

- PCAPdroid 캡처: `PCAPdroid_05_6월_17_32_02.csv`
- BLE 기기 저장: `known_device.txt`
- 다운로드 경로: `downloads/`

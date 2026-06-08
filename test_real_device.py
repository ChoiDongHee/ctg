"""
실제 M02C_EB50 안경과의 완전 통합 테스트
BLE 명령 + WiFi 파일 다운로드 + 오디오 STT
"""
import asyncio
import sys
import io
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from core.ble_manager import BLEManager
from core.wifi_transfer import WiFiTransfer
from core.audio_pipeline import AudioPipeline
from core.transcription import TranscriptionService

KNOWN_DEVICE = "3C:A6:DE:5B:EB:50"  # 이전에 저장된 주소
GLASSES_WIFI_SSID = "WF1_686725cd4952"
GLASSES_WIFI_PASSWORD = "123456789"
GLASSES_IP_CANDIDATES = [
    "192.168.49.64",  # PCAPdroid에서 확인된 실제 IP
    "192.168.49.1",
    "192.168.4.1",
    "192.168.43.1",
]

async def test_real_device():
    """실제 안경과의 통합 테스트"""

    print("=" * 80)
    print("M02C_EB50 실제 연동 통합 테스트")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 초기화
    ble = BLEManager()
    wifi = WiFiTransfer()
    audio = AudioPipeline()
    stt = TranscriptionService()

    download_dir = Path("downloads/real_test")
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ========== STEP 1: BLE 연결 ==========
        print("\n[Step 1] BLE 연결")
        print("-" * 80)
        print(f"주소: {KNOWN_DEVICE}")
        print("상태: 연결 시도 중...")

        try:
            await ble.connect(KNOWN_DEVICE)
            print("✓ BLE 연결 성공!")
            print(f"  State: {ble.state}")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"✗ BLE 연결 실패: {e}")
            print("  → 안경 전원 확인 + BLE 페어링 확인")
            return

        # ========== STEP 2: WiFi 활성화 명령 ==========
        print("\n[Step 2] WiFi 활성화")
        print("-" * 80)

        try:
            print("명령: enable_wifi_transfer()")
            await ble.enable_wifi_transfer()
            print("✓ WiFi 활성화 명령 전송")

            # WiFi 부팅 대기
            print("  (안경이 WiFi 부팅 중... 5초 대기)")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"✗ WiFi 활성화 실패: {e}")
            return

        # ========== STEP 3: WiFi IP 탐색 ==========
        print("\n[Step 3] WiFi IP 탐색")
        print("-" * 80)

        glasses_ip = None
        for ip in GLASSES_IP_CANDIDATES:
            print(f"시도: {ip}... ", end="", flush=True)
            try:
                result = await wifi.discover_ip(ip, timeout=3.0)
                if result:
                    glasses_ip = result
                    print(f"✓ 발견!")
                    break
            except Exception:
                print("✗")
                continue

        if not glasses_ip:
            print(f"✗ WiFi IP를 찾을 수 없습니다")
            print(f"  → PC가 {GLASSES_WIFI_SSID}에 연결되어 있는지 확인")
            print(f"  → 비밀번호: {GLASSES_WIFI_PASSWORD}")
            return

        print(f"✓ 안경 IP: {glasses_ip}")

        # ========== STEP 4: 파일 목록 조회 ==========
        print("\n[Step 4] 파일 목록 조회")
        print("-" * 80)

        try:
            files = await wifi.fetch_manifest(glasses_ip)
            print(f"✓ {len(files)}개 파일 발견:")

            photos = [f for f in files if f.get('type') == 'photo']
            videos = [f for f in files if f.get('type') == 'video']
            audio_files = [f for f in files if f.get('type') == 'audio']

            if photos:
                print(f"\n  사진 ({len(photos)}개):")
                for f in photos[:3]:
                    print(f"    - {f['filename']}")
                if len(photos) > 3:
                    print(f"    ... 외 {len(photos)-3}개")

            if videos:
                print(f"\n  영상 ({len(videos)}개):")
                for f in videos[:3]:
                    print(f"    - {f['filename']}")
                if len(videos) > 3:
                    print(f"    ... 외 {len(videos)-3}개")

            if audio_files:
                print(f"\n  오디오 ({len(audio_files)}개):")
                for f in audio_files[:3]:
                    print(f"    - {f['filename']}")
                if len(audio_files) > 3:
                    print(f"    ... 외 {len(audio_files)-3}개")

        except Exception as e:
            print(f"✗ 파일 목록 조회 실패: {e}")
            return

        # ========== STEP 5: 파일 다운로드 ==========
        print("\n[Step 5] 파일 다운로드")
        print("-" * 80)

        if not files:
            print("✗ 다운로드할 파일이 없습니다")
            return

        # 사진 1개, 영상 1개만 다운로드 (시간 절약)
        download_list = []
        if photos:
            download_list.append(photos[0])
        if videos:
            download_list.append(videos[0])

        print(f"다운로드: {len(download_list)}개 파일")

        import aiohttp
        async with aiohttp.ClientSession() as session:
            for f in download_list:
                filename = f['filename']
                ftype = f.get('type', 'misc')
                save_dir = download_dir / ftype

                try:
                    print(f"  ↓ {filename}... ", end="", flush=True)
                    path = await wifi.download_file(
                        session,
                        glasses_ip,
                        filename,
                        str(save_dir)
                    )
                    file_size = Path(path).stat().st_size
                    print(f"✓ ({file_size:,} bytes)")
                except Exception as e:
                    print(f"✗ ({e})")

        # ========== STEP 6: 배터리 확인 ==========
        print("\n[Step 6] 배터리 정보")
        print("-" * 80)

        try:
            await ble.get_battery()
            if ble.battery_level is not None:
                charging = "충전 중" if ble.battery_charging else "미충전"
                print(f"✓ 배터리: {ble.battery_level}% ({charging})")
            else:
                print("⚠ 배터리 정보 없음")
        except Exception as e:
            print(f"⚠ 배터리 조회 실패: {e}")

        # ========== 결과 ==========
        print("\n" + "=" * 80)
        print("[완료] 실제 안경 연동 테스트 성공!")
        print("=" * 80)
        print(f"\n다운로드 위치: {download_dir}")
        for subdir in download_dir.iterdir():
            if subdir.is_dir():
                files_in_dir = list(subdir.glob("*"))
                print(f"  {subdir.name}/: {len(files_in_dir)}개 파일")

    finally:
        # 정리
        print("\n[정리] BLE 연결 해제...", end="", flush=True)
        await ble.disconnect()
        print(" ✓")

if __name__ == "__main__":
    asyncio.run(test_real_device())

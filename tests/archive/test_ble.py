"""
BLE 진단 스크립트 — 안경 연결 전 전체 테스트
실행: python test_ble.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from bleak import BleakScanner, BleakClient

TARGET_UUIDS = [
    "7905fff0-b5ce-4e99-a40f-4b1e122d00d0",  # Primary
    "6e40fff0-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART
]


async def main():
    print("=" * 60)
    print("HeyCyan BLE 진단 스크립트")
    print("=" * 60)

    # ── 1. 스캔 ────────────────────────────────────────
    print("\n[1] BLE 스캔 중... (15초)")
    devices = await BleakScanner.discover(timeout=15.0)
    print(f"    {len(devices)}개 기기 발견\n")

    glasses_candidates = []
    for d in devices:
        name = d.name or "Unknown"
        # UUID로 안경 후보 필터
        ad = BleakScanner.discovered_devices_and_advertisement_data
        is_candidate = (
            name.startswith("G") or
            "cyan" in name.lower() or
            any(str(u).lower() in [t.lower() for t in TARGET_UUIDS]
                for u in getattr(d, "metadata", {}).get("uuids", []))
        )
        marker = " ★ 안경 후보!" if is_candidate else ""
        print(f"    {name:35} {d.address}{marker}")
        if is_candidate:
            glasses_candidates.append(d)

    # ── 2. 후보 없으면 이름 있는 것 모두 출력 ──────────
    if not glasses_candidates:
        print("\n    [주의] 이름 있는 기기:")
        named = [d for d in devices if d.name and d.name != "Unknown"]
        for d in named:
            print(f"    → {d.name} / {d.address}")

    # ── 3. 연결 시도 ────────────────────────────────────
    target = None
    if glasses_candidates:
        target = glasses_candidates[0]
        print(f"\n[2] 안경 후보 자동 연결 시도: {target.name} / {target.address}")
    else:
        # 수동 입력
        print("\n[2] 안경이 자동 감지되지 않았습니다.")
        addr = input("    연결할 MAC 주소 입력 (예: F8:F0:05:61:85:27) 또는 Enter 건너뜀: ").strip()
        if not addr:
            print("    → 건너뜀")
            return
        # 가짜 device 객체 대신 주소만 사용
        class _Dev:
            address = addr
            name = "Manual"
        target = _Dev()

    try:
        async with BleakClient(target.address) as client:
            print(f"    ✅ 연결 성공!")
            print(f"    MTU: {client.mtu_size}")

            # ── 4. 서비스/UUID 전체 출력 ─────────────
            print("\n[3] UUID 목록:")
            found_primary = False
            for svc in client.services:
                is_target = str(svc.uuid).lower() in [t.lower() for t in TARGET_UUIDS]
                marker = " ★ 안경 서비스!" if is_target else ""
                print(f"\n    [SVC] {svc.uuid}{marker}")
                if is_target:
                    found_primary = True
                for ch in svc.characteristics:
                    val_str = ""
                    if "read" in ch.properties:
                        try:
                            val = await client.read_gatt_char(ch.uuid)
                            val_str = f" = {val.hex()}"
                        except Exception:
                            pass
                    print(f"          [CHAR] {ch.uuid}  [{','.join(ch.properties)}]{val_str}")

            if found_primary:
                print("\n    ✅ HeyCyan 안경 확인됨!")
                print("    → WRITE_CHAR / NOTIFY_CHAR UUID를 ble_manager.py에 업데이트하세요")
            else:
                print("\n    ⚠ HeyCyan Primary UUID 없음 — 다른 기기일 수 있습니다")

    except Exception as e:
        print(f"\n    ❌ 연결 실패: {e}")
        print("\n    해결책:")
        print("    - 안경 A1 버튼 3초 → 전원/페어링 모드")
        print("    - 폰 블루투스 끄기")
        print("    - Windows 블루투스 설정에서 기기 제거 후 재시도")


if __name__ == "__main__":
    asyncio.run(main())

def crc16_modbus(data):
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

cmds = {
    "photo":         bytes.fromhex("020101"),
    "video-start":   bytes.fromhex("020102"),
    "audio-start":   bytes.fromhex("020108"),
    "wifi-ble":      bytes.fromhex("02010401"),
    "wifi-ap":       bytes.fromhex("02010402"),
    "preview-start": bytes.fromhex("02011402"),
    "preview-stop":  bytes.fromhex("02011501"),
    "album-count":   bytes.fromhex("0204"),
}

print("=== 패킷 검증 ===")
for name, payload in cmds.items():
    crc = crc16_modbus(payload).to_bytes(2, "little")
    pkt = bytes([0xBC, 0x41]) + len(payload).to_bytes(2, "little") + payload + crc
    print(f"{name:20} -> {pkt.hex(' ').upper()}")

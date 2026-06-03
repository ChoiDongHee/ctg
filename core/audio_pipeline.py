import asyncio
import struct
import binascii
from pathlib import Path
from typing import Optional


def wrap_opus_in_ogg(opus_data: bytes, sample_rate: int = 16000) -> bytes:
    """
    안경 raw OPUS 패킷 → Ogg Opus 컨테이너 래핑
    CyanBridge 분석: 40바이트 고정 크기 패킷
    """
    PACKET_SIZE = 40   # 고정 40바이트 (CyanBridge AlbumDownloader 확인)
    FRAME_SIZE  = 960  # 20ms @ 48kHz

    def ogg_page(data: bytes, serial: int, seq: int,
                 granule: int = 0, bos: bool = False, eos: bool = False) -> bytes:
        flags = (0x02 if bos else 0) | (0x04 if eos else 0)
        segments, remaining = [], data
        while remaining:
            seg = remaining[:255]
            segments.append(len(seg))
            remaining = remaining[len(seg):]

        header = (
            b"OggS"
            + struct.pack("<B", 0)
            + struct.pack("<B", flags)
            + struct.pack("<Q", granule)
            + struct.pack("<I", serial)
            + struct.pack("<I", seq)
            + b"\x00\x00\x00\x00"        # checksum placeholder
            + struct.pack("<B", len(segments))
            + bytes(segments)
            + data
        )
        crc = binascii.crc32(header) & 0xFFFFFFFF
        return header[:22] + struct.pack("<I", crc) + header[26:]

    serial = 0x12345678
    pages  = []

    # OpusHead
    opus_head = (
        b"OpusHead"
        + struct.pack("<B", 1)           # version
        + struct.pack("<B", 1)           # channels
        + struct.pack("<H", 312)         # pre-skip
        + struct.pack("<I", sample_rate)
        + struct.pack("<H", 0)           # output gain
        + struct.pack("<B", 0)           # mapping family
    )
    pages.append(ogg_page(opus_head, serial, 0, bos=True))

    # OpusTags
    vendor = b"HeyCyan-PC"
    opus_tags = (
        b"OpusTags"
        + struct.pack("<I", len(vendor)) + vendor
        + struct.pack("<I", 0)
    )
    pages.append(ogg_page(opus_tags, serial, 1))

    # 오디오 데이터 페이지
    granule, seq = 0, 2
    total_packets = len(opus_data) // PACKET_SIZE
    for i, offset in enumerate(range(0, len(opus_data), PACKET_SIZE)):
        packet = opus_data[offset:offset + PACKET_SIZE]
        if len(packet) < PACKET_SIZE:
            break
        granule += FRAME_SIZE
        is_last = (i == total_packets - 1)
        pages.append(ogg_page(packet, serial, seq, granule=granule, eos=is_last))
        seq += 1

    return b"".join(pages)


class AudioPipeline:

    async def _run_ffmpeg(self, *args) -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return proc.returncode, stderr

    async def opus_to_wav(self, input_path: str, output_path: Optional[str] = None) -> str:
        """
        안경 .opus → 16kHz mono WAV
        3단계 폴백:
          1) ffmpeg 직접 변환
          2) raw OPUS → Ogg 래핑 후 변환 (CyanBridge wrap_opus_in_ogg)
          3) ffmpeg -f opus 강제 포맷
        """
        if output_path is None:
            output_path = str(Path(input_path).with_suffix(".wav"))

        base_cmd = ["ffmpeg", "-y", "-ar", "16000", "-ac", "1", "-sample_fmt", "s16"]

        # 1단계
        rc, _ = await self._run_ffmpeg(*["ffmpeg", "-y", "-i", input_path, *base_cmd[3:], output_path])
        if rc == 0:
            return output_path

        # 2단계: Ogg 래핑
        ogg_path = str(Path(input_path).with_suffix(".ogg"))
        with open(input_path, "rb") as f:
            raw = f.read()
        ogg_data = wrap_opus_in_ogg(raw)
        with open(ogg_path, "wb") as f:
            f.write(ogg_data)

        rc, _ = await self._run_ffmpeg("ffmpeg", "-y", "-i", ogg_path,
                                        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                                        output_path)
        if rc == 0:
            return output_path

        # 3단계: 포맷 강제 지정
        rc, stderr = await self._run_ffmpeg("ffmpeg", "-y", "-f", "opus", "-i", input_path,
                                             "-ar", "16000", "-ac", "1", output_path)
        if rc != 0:
            raise RuntimeError(f"OPUS 변환 실패: {stderr.decode(errors='ignore')}")

        return output_path

    async def enhance_audio(self, input_wav: str, output_wav: Optional[str] = None) -> str:
        """노이즈 제거 + 음성 주파수 강조 (Whisper STT 품질 향상)"""
        if output_wav is None:
            p = Path(input_wav)
            output_wav = str(p.parent / f"{p.stem}_enhanced.wav")

        await self._run_ffmpeg(
            "ffmpeg", "-y", "-i", input_wav,
            "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=0.0001",
            output_wav,
        )
        return output_wav

    async def split_chunks(self, audio_path: str, chunk_seconds: int = 45) -> list[str]:
        """긴 오디오를 청크로 분할 (HttpTranscriptionBackend 방식)"""
        p = Path(audio_path)
        chunk_dir = p.parent / f"{p.stem}_chunks"
        chunk_dir.mkdir(exist_ok=True)

        await self._run_ffmpeg(
            "ffmpeg", "-y", "-i", audio_path,
            "-f", "segment",
            "-segment_time", str(chunk_seconds),
            "-ar", "16000", "-ac", "1",
            str(chunk_dir / "chunk_%03d.wav"),
        )
        return sorted(str(f) for f in chunk_dir.glob("chunk_*.wav"))

    async def batch_convert(self, audio_dir: str) -> list[str]:
        results = []
        for opus_file in Path(audio_dir).glob("**/*.opus"):
            try:
                wav = await self.opus_to_wav(str(opus_file))
                results.append(wav)
            except Exception as e:
                print(f"변환 실패 {opus_file.name}: {e}")
        return results

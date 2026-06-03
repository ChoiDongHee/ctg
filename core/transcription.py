import asyncio
import os
from pathlib import Path
from typing import Optional

from .audio_pipeline import AudioPipeline


class TranscriptionService:
    """
    다중 백엔드 STT (CyanBridge 방식)
    - 로컬: openai-whisper (오프라인, 한국어 large 권장)
    - 클라우드: OpenAI Whisper API
    """

    def __init__(self):
        self._model = None
        self._model_size = "large"
        self._audio = AudioPipeline()

    def set_model_size(self, size: str):
        self._model_size = size
        self._model = None  # 재로드 필요

    def _load_model(self):
        if self._model is None:
            try:
                import whisper
                self._model = whisper.load_model(self._model_size)
            except ImportError:
                raise RuntimeError("pip install openai-whisper 필요")

    async def transcribe(
        self,
        audio_path: str,
        lang: str = "ko",
        use_cloud: bool = False,
        api_key: Optional[str] = None,
        auto_convert: bool = True,
    ) -> dict:
        # OPUS → WAV 자동 변환
        path = Path(audio_path)
        if auto_convert and path.suffix.lower() == ".opus":
            audio_path = await self._audio.opus_to_wav(audio_path)

        if use_cloud:
            return await self._cloud(audio_path, lang, api_key)
        return await self._local(audio_path, lang)

    async def _local(self, audio_path: str, lang: str = "ko") -> dict:
        loop = asyncio.get_event_loop()
        self._load_model()

        def _run():
            result = self._model.transcribe(
                audio_path,
                language=lang,
                task="transcribe",
                fp16=False,
                verbose=False,
                temperature=0.0,
            )
            return {
                "text": result["text"].strip(),
                "language": result.get("language", lang),
                "segments": [
                    {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                    for s in result.get("segments", [])
                ],
                "backend": f"whisper-{self._model_size}",
            }

        return await loop.run_in_executor(None, _run)

    async def _cloud(self, audio_path: str, lang: str = "ko",
                     api_key: Optional[str] = None) -> dict:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        with open(audio_path, "rb") as f:
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=lang,
                response_format="verbose_json",
            )
        return {
            "text": resp.text,
            "language": lang,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in (resp.segments or [])
            ],
            "backend": "openai-whisper-1",
        }

    async def transcribe_chunks(
        self,
        audio_path: str,
        chunk_seconds: int = 45,
        lang: str = "ko",
        on_progress: callable = None,
    ) -> dict:
        """
        긴 오디오 청크 분할 후 순차 트랜스크립션
        HttpTranscriptionBackend.kt 방식 — 45초 청크
        """
        chunks = await self._audio.split_chunks(audio_path, chunk_seconds)
        results = []

        for i, chunk_path in enumerate(chunks):
            if on_progress:
                await on_progress(i, len(chunks), "transcribing")
            result = await self._local(chunk_path, lang)
            results.append(result)

        merged = " ".join(r["text"] for r in results)
        return {
            "text": merged,
            "language": lang,
            "chunks": results,
            "total_chunks": len(chunks),
        }

"""
Pipeline AI Service – Ham ürün isimlerini Gemini 2.5 Flash ile standardize eder ve kategorilere ayırır.

.env'den GEMINI_API_KEY veya GOOGLE_API_KEY okunur.
Veriler 10'arlı batch halinde gönderilir.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MODEL_NAME = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-1.5-flash"
API_TIMEOUT = 30.0


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Yanıt metninden JSON dizi bloğu çıkarır (```json ... ``` veya [ ... ])."""
    text = (text or "").strip()
    # Markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # Direkt [ ile başlayan dizi
    start = text.find("[")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


class ProductNormalizerService:
    """
    Ham ürün isimlerini Gemini 2.5 Flash ile standart isim ve kategoriye dönüştürür.
    API key .env'den (GEMINI_API_KEY veya GOOGLE_API_KEY) okunur.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._model: str | None = None
        api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if not api_key:
            logger.warning(
                "[ProductNormalizerService] GEMINI_API_KEY / GOOGLE_API_KEY .env'de tanımlı değil."
            )
            return
        try:
            from google import genai
            from google.genai import types

            self._genai = genai
            self._types = types
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(api_version="v1"),
            )
            self._model = MODEL_NAME
        except Exception as e:
            logger.exception("[ProductNormalizerService] Client başlatılamadı: %s", e)
            self._client = None

    def is_available(self) -> bool:
        return self._client is not None and self._model is not None

    async def _call_gemini_batch(self, product_names: list[str]) -> list[dict[str, Any]]:
        """Tek bir batch (en fazla BATCH_SIZE) için Gemini'yi çağırır."""
        if not self._client or not self._model:
            return []
        prompt = f"""Aşağıdaki market ürün isimlerini standart Türkçe isimlere çevir ve her biri için tek bir kategori belirle.
Kategoriler örnek: Süt Ürünleri, Et ve Tavuk, İçecek, Atıştırmalık, Temel Gıda, Kahvaltılık, Dondurulmuş Gıda, Temizlik, Kişisel Bakım, Diğer.

Ürün isimleri (her satırda bir tane):
{chr(10).join(product_names)}

Yanıtı SADECE aşağıdaki JSON dizisi formatında ver, başka metin ekleme:
[{{"original": "orijinal isim", "standard_name": "standart isim", "category": "Kategori"}}, ...]
"""
        try:
            await asyncio.sleep(0.5)
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=self._types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=2048,
                    ),
                ),
                timeout=API_TIMEOUT,
            )
            text = (response.text or "").strip()
            if not text:
                return []
            out = _extract_json_array(text)
            return out if isinstance(out, list) else []
        except Exception as e:
            logger.warning("[ProductNormalizerService] Gemini batch hatası: %s", e)
            if "404" in str(e) and self._model == MODEL_NAME:
                self._model = FALLBACK_MODEL
                logger.info("[ProductNormalizerService] Fallback model: %s", FALLBACK_MODEL)
            return []

    async def normalize_products(
        self,
        product_names: list[str],
        *,
        batch_size: int = BATCH_SIZE,
    ) -> list[dict[str, Any]]:
        """
        Ham ürün isimlerini 10'arlı gruplar halinde Gemini'ye gönderir;
        standart isim ve kategori döndürür.

        Args:
            product_names: Ham ürün isimleri listesi
            batch_size: Her batch'te gönderilecek sayı (varsayılan 10)

        Returns:
            [{"original": str, "standard_name": str, "category": str}, ...]
            API yoksa veya hata olursa original isim aynen, category "Diğer" döner.
        """
        if not product_names:
            return []
        if not self.is_available():
            return [
                {"original": n, "standard_name": n.strip()[:300], "category": "Diğer"}
                for n in product_names
            ]

        result: list[dict[str, Any]] = []
        for i in range(0, len(product_names), batch_size):
            batch = product_names[i : i + batch_size]
            batch_result = await self._call_gemini_batch(batch)
            # Eşleme: sırayla original'a göre (Gemini sırayı bozabilir)
            name_to_clean: dict[str, dict] = {r.get("original", ""): r for r in batch_result if isinstance(r, dict)}
            for name in batch:
                clean = name_to_clean.get(name) or name_to_clean.get(name.strip())
                if clean and isinstance(clean, dict):
                    result.append({
                        "original": name,
                        "standard_name": (clean.get("standard_name") or name or "").strip()[:300],
                        "category": (clean.get("category") or "Diğer").strip()[:100],
                    })
                else:
                    result.append({
                        "original": name,
                        "standard_name": (name or "").strip()[:300],
                        "category": "Diğer",
                    })
        return result

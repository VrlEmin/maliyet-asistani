"""
AIService – Google Gemini API ile tasarruf analizi ve yatırım koçluğu.

Production-ready implementation with:
- API v1 endpoint (v1beta removed)
- Model validation on startup (client.models.list())
- Timeout and retry mechanisms
- Proper fallback chain: gemini-2.5-flash -> gemini-1.5-pro
- Clean error handling with fallback responses
- Type hints and clean architecture

Elde edilen fiyat farklarını analiz ederek kullanıcıya:
  1. Ne kadar tasarruf yapabileceğini açıklar.
  2. Bu tasarrufu hangi yatırım araçlarına yönlendirebileceğini önerir.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional

from google import genai
from google.genai import types

from models.database import settings

logger = logging.getLogger(__name__)

# Model fallback chain (priority order)
MODEL_FALLBACK_CHAIN = [
    "gemini-2.5-flash",
    "gemini-1.5-pro",
]

# API configuration
API_TIMEOUT_SECONDS = 30.0
API_RETRY_ATTEMPTS = 3
API_RETRY_DELAY_SECONDS = 2.0
RATE_LIMIT_DELAY_SECONDS = 2.0


def _log_gemini_error_url(exc: BaseException, context: str = "") -> None:
    """404 vb. hatalarda terminale tam URL ve hata detayını yazdır."""
    msg = str(exc)
    url = None
    
    # exception.request.url (httpx)
    if hasattr(exc, "request") and exc.request is not None:
        url = getattr(exc.request, "url", None)
        if url is not None:
            url = str(url)
    
    # exception.response.request.url
    if url is None and hasattr(exc, "response") and exc.response is not None:
        req = getattr(exc.response, "request", None)
        if req is not None:
            u = getattr(req, "url", None)
            if u is not None:
                url = str(u)
    
    # __cause__ zincirinde ara
    if url is None and hasattr(exc, "__cause__") and exc.__cause__ is not None:
        c = exc.__cause__
        if hasattr(c, "request") and c.request is not None:
            u = getattr(c.request, "url", None)
            if u is not None:
                url = str(u)
    
    if url:
        url_line = f"  Tam URL: {url}"
    else:
        url_line = f"  (URL bulunamadı) __dict__={getattr(exc, '__dict__', {})!r}"
    
    lines = [
        f"[AIService] Gemini API hatası {context}",
        f"  Hata: {type(exc).__name__}: {msg}",
        url_line,
    ]
    out = "\n".join(lines)
    logger.error(out)
    print(out, file=sys.stderr)


class AIService:
    """
    Gemini API servisi - production-ready implementation.
    
    Features:
    - Model validation on startup
    - Automatic fallback chain
    - Timeout and retry mechanisms
    - Clean error handling
    """

    def __init__(self) -> None:
        """
        AIService'i başlatır.
        
        - API key kontrolü yapar
        - Client'ı oluşturur (v1 API, no hardcoded endpoints)
        - Model validation yapar (client.models.list())
        - Kullanılabilir modeli belirler
        """
        api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if not api_key:
            logger.warning(
                "[AIService] API anahtarı boş! GEMINI_API_KEY veya GOOGLE_API_KEY .env dosyasında tanımlı olmalı."
            )
            self.client = None
            self.available_model: Optional[str] = None
            return
        
        # Client oluştur (API v1 kullanılır)
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1")
        )
        
        # Model validation (async, startup'ta yapılacak)
        self.available_model: Optional[str] = None
        
        logger.info(
            "[AIService] Başlatıldı (API v1, model validation başlatılıyor...)"
        )

    async def _validate_and_select_model(self) -> Optional[str]:
        """
        client.models.list() ile mevcut modelleri kontrol eder ve
        fallback chain'den ilk kullanılabilir modeli seçer.
        
        Returns:
            Kullanılabilir model adı veya None
        """
        if not self.client:
            logger.error("[AIService] Client başlatılmamış, model validation yapılamıyor.")
            return None
        
        try:
            logger.info("[AIService] Model validation başlatılıyor...")
            
            # Mevcut modelleri listele
            models_response = await self.client.aio.models.list()
            available_models = {model.name for model in models_response}
            
            logger.info(
                "[AIService] Toplam %d model bulundu. Fallback chain kontrol ediliyor...",
                len(available_models),
            )
            
            # Fallback chain'den ilk kullanılabilir modeli bul
            for model_name in MODEL_FALLBACK_CHAIN:
                # SDK otomatik "models/" prefix ekler, bu yüzden kontrol ederken
                # hem "models/NAME" hem de "NAME" formatlarını kontrol et
                full_name = f"models/{model_name}"
                
                if full_name in available_models or model_name in available_models:
                    logger.info(
                        "[AIService] Model seçildi: %s (fallback chain: %s)",
                        model_name,
                        MODEL_FALLBACK_CHAIN,
                    )
                    return model_name
            
            logger.error(
                "[AIService] Fallback chain'deki hiçbir model bulunamadı. "
                "Mevcut modeller: %s",
                sorted(available_models)[:10],  # İlk 10'unu göster
            )
            return None
            
        except Exception as e:
            logger.exception("[AIService] Model validation hatası: %s", e)
            # Validation başarısız olsa bile fallback chain'in ilk modelini dene
            logger.warning(
                "[AIService] Model validation başarısız, fallback chain'in ilk modeli kullanılacak: %s",
                MODEL_FALLBACK_CHAIN[0],
            )
            return MODEL_FALLBACK_CHAIN[0]

    async def _ensure_model_validated(self) -> bool:
        """
        Model validation yapılmadıysa yapar.
        
        Returns:
            True if model is available, False otherwise
        """
        if self.available_model is None:
            self.available_model = await self._validate_and_select_model()
        
        if not self.available_model:
            logger.error("[AIService] Kullanılabilir model yok!")
            return False
        
        return True

    async def _generate_with_retry(
        self,
        model: str,
        prompt: str,
        config: types.GenerateContentConfig,
        max_retries: int = API_RETRY_ATTEMPTS,
    ) -> Optional[str]:
        """
        Retry mekanizması ile Gemini API çağrısı yapar.
        
        Args:
            model: Model adı
            prompt: Prompt metni
            config: GenerateContentConfig
            max_retries: Maksimum retry sayısı
        
        Returns:
            Response text veya None
        """
        if not self.client:
            logger.error("[AIService] Client başlatılmamış.")
            return None
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(max_retries):
            try:
                # Rate limit önlemi
                if attempt > 0:
                    await asyncio.sleep(API_RETRY_DELAY_SECONDS * attempt)
                else:
                    await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)
                
                # API çağrısı (timeout SDK tarafından yönetilir)
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=API_TIMEOUT_SECONDS,
                )
                
                result = (response.text or "").strip()
                if result:
                    if attempt > 0:
                        logger.info(
                            "[AIService] Retry başarılı (attempt %d/%d)",
                            attempt + 1,
                            max_retries,
                        )
                    return result
                
                logger.warning("[AIService] Gemini boş cevap döndü (model=%s)", model)
                
            except asyncio.TimeoutError:
                last_exception = TimeoutError(f"API timeout after {API_TIMEOUT_SECONDS}s")
                logger.warning(
                    "[AIService] API timeout (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    last_exception,
                )
                if attempt < max_retries - 1:
                    continue
                
            except Exception as exc:
                last_exception = exc
                exc_str = str(exc).lower()
                
                # 404 - Model bulunamadı
                is_404 = "404" in str(exc) or "not found" in exc_str
                if is_404:
                    _log_gemini_error_url(exc, f"(model={model}, attempt={attempt + 1})")
                    logger.error(
                        "[AIService] Model bulunamadı (404): %s (attempt %d/%d)",
                        model,
                        attempt + 1,
                        max_retries,
                    )
                    # 404 durumunda retry yapmaya gerek yok, fallback model denenmeli
                    raise
                
                # 429 - Rate limit
                is_429 = (
                    "429" in str(exc)
                    or "rate" in exc_str
                    or "retry" in exc_str
                    or "resource exhausted" in exc_str
                )
                if is_429:
                    logger.warning(
                        "[AIService] Rate limit (429) (attempt %d/%d), retry ediliyor...",
                        attempt + 1,
                        max_retries,
                    )
                    if attempt < max_retries - 1:
                        # Rate limit için daha uzun bekle
                        await asyncio.sleep(API_RETRY_DELAY_SECONDS * 2)
                        continue
                    else:
                        logger.error("[AIService] Rate limit hatası devam ediyor, fallback'e geçiliyor.")
                        raise
                
                # Diğer hatalar
                logger.warning(
                    "[AIService] API hatası (attempt %d/%d): %s - %s",
                    attempt + 1,
                    max_retries,
                    type(exc).__name__,
                    exc,
                )
                if attempt < max_retries - 1:
                    continue
        
        # Tüm retry'lar başarısız
        if last_exception:
            logger.error(
                "[AIService] Tüm retry'lar başarısız (max_retries=%d): %s",
                max_retries,
                last_exception,
            )
        return None

    # ── Tasarruf Analizi ─────────────────────────────────────────────────────

    async def analyze_savings(self, comparison_data: dict[str, Any]) -> str:
        """
        Fiyat karşılaştırma verisini analiz ederek Türkçe tasarruf raporu üretir.

        Args:
            comparison_data: BotManager.search_all() çıktısı.

        Returns:
            Kullanıcı dostu Türkçe analiz metni.
        """
        if not await self._ensure_model_validated():
            return "AI servisi şu an kullanılamıyor. Ham fiyat verileri yukarıda."
        
        prompt = self._build_savings_prompt(comparison_data)
        config = types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=1024,
            top_p=0.9,
        )
        
        try:
            result = await self._generate_with_retry(
                model=self.available_model,
                prompt=prompt,
                config=config,
            )
            return result or "Analiz oluşturulamadı."
        except Exception as exc:
            logger.warning("[AIService] Tasarruf analizi (Gemini) başarısız: %s", exc)
            return "AI özeti şu an kullanılamıyor. Ham fiyat verileri yukarıda."

    # ── Karşılaştırma Özeti ─────────────────────────────────────────────────

    async def compare_and_summarize(
        self,
        query: str,
        results: list[dict[str, Any]],
        cheapest: dict[str, Any] | None,
        most_expensive: dict[str, Any] | None,
        potential_saving: float,
        nearest_market: dict[str, Any] | None = None,
    ) -> str:
        """
        Tüm market sonuçlarını Gemini'ye gönderir; iki cümlelik özet döner.
        """
        if not await self._ensure_model_validated():
            return self._fallback_compare_summary(
                query=query,
                cheapest=cheapest,
                most_expensive=most_expensive,
                potential_saving=potential_saving,
                nearest_market=nearest_market,
                error_hint=None,
            )
        
        prompt = self._build_compare_prompt(
            query=query,
            results=results,
            cheapest=cheapest,
            most_expensive=most_expensive,
            potential_saving=potential_saving,
            nearest_market=nearest_market,
        )
        config = types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=512,
            top_p=0.9,
        )
        
        try:
            result = await self._generate_with_retry(
                model=self.available_model,
                prompt=prompt,
                config=config,
            )
            return result or "Özet oluşturulamadı."
        except Exception as exc:
            logger.warning("[AIService] Karşılaştırma özeti (Gemini) başarısız: %s", exc)
            return self._fallback_compare_summary(
                query=query,
                cheapest=cheapest,
                most_expensive=most_expensive,
                potential_saving=potential_saving,
                nearest_market=nearest_market,
                error_hint=exc,
            )

    # ── Akıllı Alışveriş Tavsiyesi ───────────────────────────────────────────

    async def generate_shopping_advice(
        self,
        user_query: str,
        processed_data: list[dict[str, Any]],
    ) -> str:
        """
        İşlenmiş ürün verilerini tablo formatında göstererek doğal dilde
        akıllı alışveriş tavsiyesi verir.

        Args:
            user_query: Kullanıcının arama sorgusu
            processed_data: DataProcessor'dan gelen işlenmiş ürün listesi

        Returns:
            Türkçe alışveriş tavsiyesi metni (markdown table formatında)
        """
        if not processed_data:
            logger.info("[AIService] generate_shopping_advice: processed_data boş, AI çağrılmadı")
            return "Uygun ürün bulunamadığı için tavsiye oluşturulamadı."
        
        if not await self._ensure_model_validated():
            logger.warning("[AIService] Model validation başarısız, markdown tablosu döndürülüyor")
            return self._build_markdown_table_advice(user_query, processed_data)
        
        # Kota tasarrufu: sadece en ucuz 5 ürünü gönder
        processed_data = processed_data[:5]
        logger.info(
            "[AIService] generate_shopping_advice çağrılıyor (query: %s, ürün sayısı: %d)",
            user_query,
            len(processed_data),
        )
        prompt = self._build_shopping_advice_prompt(user_query, processed_data)
        
        config = types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=1024,
            top_p=0.9,
        )
        
        # Fallback chain ile dene
        for model_name in MODEL_FALLBACK_CHAIN:
            # Eğer validation yapıldıysa sadece available_model'i kullan
            if self.available_model and model_name != self.available_model:
                # Validation yapıldıysa sadece validated model'i kullan
                if model_name in MODEL_FALLBACK_CHAIN[:MODEL_FALLBACK_CHAIN.index(self.available_model) + 1]:
                    continue
            
            try:
                result = await self._generate_with_retry(
                    model=model_name,
                    prompt=prompt,
                    config=config,
                )
                if result:
                    return result
                
            except Exception as exc:
                exc_str = str(exc).lower()
                is_404 = "404" in str(exc) or "not found" in exc_str
                
                if is_404:
                    _log_gemini_error_url(exc, f"(generate_shopping_advice, model={model_name})")
                    logger.warning(
                        "[AIService] Model %s ile 404, fallback chain devam ediyor...",
                        model_name,
                    )
                    # Fallback chain'deki bir sonraki modeli dene
                    continue
                
                # 429 veya diğer hatalar için markdown tablosu döndür
                logger.error(
                    "[AIService] Model %s ile hata (404 değil), markdown tablosu döndürülüyor: %s",
                    model_name,
                    exc,
                )
                return self._build_markdown_table_advice(user_query, processed_data)
        
        # Tüm modeller başarısız olduysa markdown tablosu döndür
        logger.error("[AIService] Tüm Gemini modelleri başarısız, markdown tablosu döndürülüyor")
        return self._build_markdown_table_advice(user_query, processed_data)

    # ── Yatırım Koçluğu ─────────────────────────────────────────────────────

    async def investment_coaching(
        self,
        saved_amount: float,
        monthly_budget: Optional[float] = None,
    ) -> str:
        """
        Tasarruf edilen miktarı değerlendirecek yatırım önerileri sunar.

        Args:
            saved_amount: Tek alışverişte tasarruf edilen miktar (TL).
            monthly_budget: Aylık market bütçesi (TL, opsiyonel).

        Returns:
            Yatırım koçluğu metni.
        """
        if not await self._ensure_model_validated():
            return "AI servisi şu an kullanılamıyor."
        
        prompt = self._build_investment_prompt(saved_amount, monthly_budget)
        config = types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=1024,
            top_p=0.9,
        )
        
        try:
            result = await self._generate_with_retry(
                model=self.available_model,
                prompt=prompt,
                config=config,
            )
            return result or "Yatırım önerisi oluşturulamadı."
        except Exception as exc:
            logger.warning("[AIService] Yatırım koçluğu (Gemini) başarısız: %s", exc)
            return "AI özeti şu an kullanılamıyor. Ham fiyat verileri kullanılabilir."

    # ── AI Re-Ranking ────────────────────────────────────────────────────────

    async def rerank_products(
        self,
        query: str,
        products: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Gemini ile AI re-ranking. Şu an devre dışı – kota tasarrufu için
        gelen listeyi olduğu gibi döndürür (tek mesaj = tek AI çağrısı).
        """
        return products

    # ── Prompt Şablonları ────────────────────────────────────────────────────

    @staticmethod
    def _build_markdown_table_advice(user_query: str, processed_data: list[dict[str, Any]]) -> str:
        """
        Sadece markdown tablosu döndürür. Hiçbir açıklama metni yok.
        """
        if not processed_data:
            return ""

        # Verileri hazırla ve birim fiyata göre sırala
        products_with_unit = []
        for p in processed_data:
            name = p.get("product_name", "")
            market = p.get("market_name", "")
            price = p.get("price", 0)
            unit_price_kg = p.get("normalized_price_per_kg")
            if unit_price_kg is None:
                unit_price_100 = p.get("unit_price") or p.get("unit_price_per_100")
                if unit_price_100 is not None:
                    unit_price_kg = unit_price_100 * 10  # 100g -> 1kg için yaklaşık
            products_with_unit.append({
                "name": name,
                "market": market,
                "price": price,
                "unit_price_kg": unit_price_kg,
            })

        # En ucuzdan en pahalıya sırala
        products_with_unit.sort(
            key=lambda x: x["unit_price_kg"] if x["unit_price_kg"] is not None else float("inf")
        )

        # Sadece markdown tablosu
        lines = [
            "| Ürün Adı | Market | Birim Fiyat (TL/kg) | Toplam Fiyat |",
            "| --- | --- | --- | --- |",
        ]

        for p in products_with_unit:
            name = (p["name"] or "")[:50]
            market = p["market"] or ""
            price = p["price"] or 0
            unit_price_kg = p["unit_price_kg"]
            unit_str = f"{unit_price_kg:.2f}" if unit_price_kg is not None else "-"
            lines.append(f"| {name} | {market} | {unit_str} | {price:.2f} TL |")

        return "\n".join(lines)

    @staticmethod
    def _build_shopping_advice_prompt(
        user_query: str,
        processed_data: list[dict[str, Any]],
    ) -> str:
        """Alışveriş tavsiyesi için prompt oluşturur."""
        if not processed_data:
            return f"""Kullanıcı "{user_query}" aramış ancak ürün verisi bulunamadı.
Kısa ve nazikçe ürün bulunamadığını belirten bir mesaj yaz."""

        # Veri hazırlama: birim fiyatları hesapla (TL/kg)
        products_with_unit = []
        for p in processed_data:
            name = p.get("product_name", "")
            market = p.get("market_name", "")
            price = p.get("price", 0)
            unit_price_kg = p.get("normalized_price_per_kg")
            if unit_price_kg is None:
                unit_price_100 = p.get("unit_price") or p.get("unit_price_per_100")
                if unit_price_100 is not None:
                    unit_price_kg = unit_price_100 * 10
            products_with_unit.append({
                "name": name,
                "market": market,
                "price": price,
                "unit_price_kg": unit_price_kg,
            })

        # En ucuzdan en pahalıya sırala
        products_with_unit.sort(
            key=lambda x: x["unit_price_kg"] if x["unit_price_kg"] is not None else float("inf")
        )

        # Markdown tablo formatında veri hazırla
        rows = []
        rows.append("| Ürün Adı | Market | Birim Fiyat (TL/kg) | Toplam Fiyat |")
        rows.append("|----------|--------|---------------------|--------------|")

        unit_prices: list[float] = []
        for p in products_with_unit:
            name = (p["name"] or "")[:50]
            market = p["market"] or ""
            price = p["price"] or 0
            unit_price_kg = p["unit_price_kg"]
            unit_str = f"{unit_price_kg:.2f}" if unit_price_kg is not None else "-"
            rows.append(f"| {name} | {market} | {unit_str} | {price:.2f} TL |")
            if unit_price_kg is not None and unit_price_kg > 0:
                unit_prices.append(float(unit_price_kg))

        table = "\n".join(rows)
        
        # Yüzde fark hesapla
        percent_diff_info = ""
        if len(unit_prices) >= 2:
            min_up = min(unit_prices)
            max_up = max(unit_prices)
            if min_up > 0:
                percent_diff = ((max_up - min_up) / min_up) * 100
                percent_diff_info = f"\nBirim fiyatlar arasında %{percent_diff:.1f} fark var (en ucuz: {min_up:.2f} TL/kg, en pahalı: {max_up:.2f} TL/kg)."

        return f"""Sen bir Türk market alışveriş danışmanısın. Aşağıdaki tabloda "{user_query}" aramasına ait ürünler var.

ÖNEMLİ TALİMATLAR:
1. Yanıtını MUTLAKA markdown tablosu formatında ver. Tablo şu sütunları içermeli:
   - Ürün Adı | Market | Birim Fiyat (TL/kg) | Toplam Fiyat
2. En ucuz ürünü listenin EN BAŞINA koy (birim fiyata göre).
3. Fiyat farklarını YÜZDE olarak yorumla (örn: "En pahalı ürün en ucuzdan %X daha pahalı").
4. Tablo formatı şu şekilde olmalı:
   | Ürün Adı | Market | Birim Fiyat (TL/kg) | Toplam Fiyat |
   |----------|--------|---------------------|--------------|
   | ... | ... | ... | ... |
5. Tablodan sonra kısa bir yorum ekle (2-3 cümle).

## Kullanıcı sorgusu: "{user_query}"

## Ürün verileri:

{table}
{percent_diff_info}

Lütfen markdown tablosu formatında cevap ver ve en ucuz ürünü başa koy. Fiyat farklarını yüzde olarak açıkla.
"""

    @staticmethod
    def _fallback_compare_summary(
        query: str,
        cheapest: dict[str, Any] | None,
        most_expensive: dict[str, Any] | None,
        potential_saving: float,
        nearest_market: dict[str, Any] | None,
        error_hint: Optional[Exception],
    ) -> str:
        """Gemini kota/hatada kullanılacak kısa özet (veriden üretilir)."""
        parts = []
        if cheapest:
            parts.append(
                f"Şu an en ucuz {query} {cheapest.get('market_name', '')} marketinde, "
                f"{cheapest.get('price', 0):,.2f} TL."
            )
        if most_expensive and most_expensive != cheapest:
            parts.append(
                f"En pahalı {most_expensive.get('market_name', '')} marketinde "
                f"{most_expensive.get('price', 0):,.2f} TL."
            )
        if potential_saving > 0:
            parts.append(f"Aradaki fark {potential_saving:,.2f} TL.")
        if nearest_market:
            name = nearest_market.get("branch_name") or nearest_market.get("name", "")
            dist = nearest_market.get("distance_km")
            parts.append(f"Konumuna en yakın {name}" + (f" ({dist} km)." if dist is not None else "."))
        if not parts:
            return "AI özeti şu an kullanılamıyor."
        return "AI özeti şu an kullanılamıyor. " + " ".join(parts)

    @staticmethod
    def _build_compare_prompt(
        query: str,
        results: list[dict[str, Any]],
        cheapest: dict[str, Any] | None,
        most_expensive: dict[str, Any] | None,
        potential_saving: float,
        nearest_market: dict[str, Any] | None,
    ) -> str:
        lines = []
        for r in results[:15]:
            g = r.get("gramaj")
            g_str = f" ({g} g)" if g else ""
            up = r.get("unit_price_per_kg")
            up_str = f" [birim: {up} TL/kg]" if up else ""
            lines.append(f"  - {r['market_name']}: {r['product_name']} → {r['price']} TL{g_str}{up_str}")
        table = "\n".join(lines) if lines else "  (Veri yok)"
        nearest_str = ""
        if nearest_market:
            nearest_str = f"""
## Kullanıcıya en yakın market: {nearest_market.get('branch_name', nearest_market.get('name', ''))} ({nearest_market.get('name', '')})
## Mesafe: {nearest_market.get('distance_km')} km, Adres: {nearest_market.get('address', '')}
"""
        return f"""Sen bir market fiyat asistanısın. Verilen listeye göre Türkçe, kısa ve net bir özet yaz.
ÖNEMLİ KURALLAR:
- Fiyatlar kuruş DEĞİL, TL cinsindendir. 189.95 demek 189 lira 95 kuruş demektir.
- En ucuz ürünü seçerken anahtar kelimeye ("{query}") en yakın ürünü dikkate al.
  Mesela kullanıcı "tavuk göğsü" aramışsa; noodle, çorba, ped gibi alakasız ürünleri dikkate ALMA.
- Gramaj ve birim fiyat (TL/kg) verisi varsa, 1 kg fiyatı üzerinden karşılaştır.

## Aranan ürün: "{query}"

## Tüm market fiyatları (TL):
{table}

## En ucuz: {cheapest['market_name'] + ' → ' + str(cheapest['price']) + ' TL' if cheapest else 'Yok'}
## En pahalı: {most_expensive['market_name'] + ' → ' + str(most_expensive['price']) + ' TL' if most_expensive else 'Yok'}
## Fiyat farkı: {potential_saving} TL
{nearest_str}

Çıktı formatı (tam 2 cümle):
1. "Şu an en ucuz [ürün adı] X marketinde, kilosu/adeti Y TL." (birim fiyat varsa kg başına belirt)
2. Konuma en yakın market verildiyse: "Senin konumuna en yakın olan ise Z marketi; aradaki fiyat farkı W TL." Verilmediyse en pahalı-en ucuz farkını belirt.
Başka açıklama ekleme.
"""

    @staticmethod
    def _build_savings_prompt(data: dict[str, Any]) -> str:
        query = data.get("query", "ürün")
        results = data.get("results", [])
        cheapest = data.get("cheapest")
        most_expensive = data.get("most_expensive")
        saving = data.get("potential_saving", 0)

        # Fiyat tablosu oluştur
        price_lines = []
        for r in results[:10]:
            price_lines.append(
                f"  - {r['market_name']}: {r['product_name']} → {r['price']} TL"
            )
        price_table = "\n".join(price_lines) if price_lines else "  (Veri bulunamadı)"

        return f"""Sen bir Türk market fiyat analiz uzmanısın. Kullanıcıya samimi, 
anlaşılır ve pratik bilgiler sun. Yanıtını Türkçe ver.
Lütfen fiyatların kuruş değil TL olduğunu varsay; aşağıdaki fiyatlar TL cinsindendir.

## Aranan Ürün: "{query}"

## Market Fiyatları (TL):
{price_table}

## En Ucuz: {cheapest['market_name'] + ' → ' + str(cheapest['price']) + ' TL' if cheapest else 'Bulunamadı'}
## En Pahalı: {most_expensive['market_name'] + ' → ' + str(most_expensive['price']) + ' TL' if most_expensive else 'Bulunamadı'}
## Potansiyel Tasarruf: {saving} TL

Lütfen şunları yap:
1. Fiyat farklarını özetle ve en uygun marketi öner.
2. Bu tasarrufun aylık ve yıllık bazda ne anlama geldiğini hesapla 
   (haftada 2 kez alışveriş varsayımıyla).
3. Ürün kalitesi ve fiyat-performans açısından kısa bir değerlendirme yap.
4. Samimi ve motive edici bir dil kullan.
"""

    @staticmethod
    def _build_investment_prompt(
        saved_amount: float,
        monthly_budget: Optional[float] = None,
    ) -> str:
        budget_info = ""
        if monthly_budget:
            monthly_saving_estimate = saved_amount * 8
            budget_info = f"""
## Aylık Market Bütçesi: {monthly_budget} TL
## Tahmini Aylık Tasarruf: {monthly_saving_estimate} TL
## Yıllık Tasarruf Potansiyeli: {monthly_saving_estimate * 12} TL
"""

        return f"""Sen bir Türk bireysel finans danışmanısın. Kullanıcının market 
alışverişlerinden tasarruf ettiği parayı değerlendirmesi için yatırım 
önerileri sunacaksın. Yanıtını Türkçe ver.

## Tek Alışverişte Tasarruf: {saved_amount} TL
{budget_info}

Lütfen şunları yap:
1. Bu tasarrufun önemini vurgula (küçük birikimler, büyük sonuçlar).
2. Türkiye'deki güncel yatırım araçlarını öner:
   - Vadeli mevduat (TL / döviz)
   - Devlet tahvili / hazine bonosu
   - Altın (gram altın, küçük yatırımcı için)
   - Borsa (BIST hisse senetleri, fonlar)
   - Kripto para (düşük risk oranıyla)
3. Risk profiline göre 3 farklı strateji öner:
   - Düşük risk (muhafazakar)
   - Orta risk (dengeli)
   - Yüksek risk (agresif)
4. Her strateji için tahmini yıllık getiri oranı ver.
5. Samimi, motive edici ve eğitici bir dil kullan.
6. ⚠️ Bunun yatırım tavsiyesi olmadığını, bilgilendirme amaçlı olduğunu belirt.
"""

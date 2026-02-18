"""
Telegram Bot – Maliyet Asistanı arayüzü.

Production-ready implementation with:
- Async main + application.initialize/start/stop lifecycle
- Error handler for unhandled exceptions
- Graceful shutdown
- Instance lock to prevent multiple polling instances
- Webhook cleanup on startup

Çalıştırma (backend dizininden):
    python -m services.telegram_bot

Docker:
    docker compose run --rm app python -m services.telegram_bot
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.error import Conflict, NetworkError, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# backend dizinini Python path'e ekle
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from models.database import close_redis, init_redis, settings
from services.ai_service import AIService
from services.bot_manager import BotManager
from services.data_processor import DataProcessor
from services.filter_service import FilterService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Global servis referansları
bot_manager: Optional[BotManager] = None
data_processor: Optional[DataProcessor] = None
filter_service: Optional[FilterService] = None
ai_service: Optional[AIService] = None

# Application instance
application: Optional[Application] = None

# Graceful shutdown flag
_shutdown_event = asyncio.Event()

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _acquire_instance_lock() -> bool:
    """
    Tek bir bot instance'ının çalışmasını garanti eder.
    Lock file kullanarak birden fazla polling instance'ı engeller.
    
    Returns:
        True if lock acquired, False if another instance is running
    """
    lock_file = Path("/tmp/telegram_bot.lock")
    pid_file = Path("/tmp/telegram_bot.pid")
    
    # Lock file varsa ve process hala çalışıyorsa
    if lock_file.exists():
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                # Process hala çalışıyor mu kontrol et
                try:
                    os.kill(old_pid, 0)  # Signal 0 = process existence check
                    logger.error(
                        "Başka bir bot instance çalışıyor (PID: %d). "
                        "Lütfen önce onu kapatın: kill %d",
                        old_pid,
                        old_pid,
                    )
                    return False
                except ProcessLookupError:
                    # Process ölmüş, lock dosyalarını temizle
                    logger.warning("Eski lock dosyası bulundu (process ölmüş), temizleniyor...")
                    lock_file.unlink(missing_ok=True)
                    pid_file.unlink(missing_ok=True)
            except (ValueError, OSError) as e:
                logger.warning("Lock dosyası okunamadı, temizleniyor: %s", e)
                lock_file.unlink(missing_ok=True)
                pid_file.unlink(missing_ok=True)
        else:
            # Lock var ama PID yok, temizle
            logger.warning("Lock dosyası var ama PID dosyası yok, temizleniyor...")
            lock_file.unlink(missing_ok=True)
    
    # Lock oluştur
    try:
        lock_file.touch()
        pid_file.write_text(str(os.getpid()))
        logger.info("Instance lock alındı (PID: %d)", os.getpid())
        return True
    except OSError as e:
        logger.error("Lock dosyası oluşturulamadı: %s", e)
        return False


def _release_instance_lock() -> None:
    """Lock dosyalarını temizle."""
    lock_file = Path("/tmp/telegram_bot.lock")
    pid_file = Path("/tmp/telegram_bot.pid")
    lock_file.unlink(missing_ok=True)
    pid_file.unlink(missing_ok=True)
    logger.info("Instance lock serbest bırakıldı")


def _setup_signal_handlers() -> None:
    """SIGINT ve SIGTERM için graceful shutdown handler'ları kur."""
    
    def signal_handler(signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("%s sinyali alındı, graceful shutdown başlatılıyor...", sig_name)
        _shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _format_top3_and_advice(query: str, products: list[dict], advice: str) -> str:
    """En ucuz 3 ürünü ve AI özetini formatlar. Telegram 4096 karakter limiti için kısaltır."""
    lines = [f"🔍 {query} araması", ""]
    if products:
        lines.append("💰 En ucuz 3 seçenek:")
        for i, p in enumerate(products[:3], 1):
            name = (p.get("product_name") or "")[:40]
            price = p.get("price", 0)
            market = p.get("market_name", "")
            unit_price = p.get("unit_price") or p.get("unit_price_per_100")
            if unit_price is not None:
                lines.append(f"{i}. {name} - {price:.2f} TL (birim: {unit_price:.2f} TL) | {market}")
            else:
                lines.append(f"{i}. {name} - {price:.2f} TL | {market}")
        lines.append("")
    else:
        lines.append("Ürün bulunamadı.")
        lines.append("")
    lines.append("💡 AI Özeti:")
    lines.append(advice or "AI özeti alınamadı.")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
        text = text[: TELEGRAM_MAX_MESSAGE_LENGTH - 20] + "\n\n[...kısaltıldı]"
    return text


def _format_basket_result(
    queries: list[str],
    recommendations: list[dict],
    total_basket_tl: float,
    ai_summary: str,
) -> str:
    """Akıllı sepet çıktısı: ürün bazlı en ucuz + toplam sepet tutarı + AI özeti."""
    lines = ["🛒 Akıllı Sepet Özeti", ""]
    for rec in recommendations:
        product = rec.get("product", "")
        market = rec.get("market", "")
        product_name = (rec.get("product_name", "") or "")[:45]
        price = rec.get("price", 0)
        lines.append(f"• {product}: {market} – {product_name}, {price:.2f} TL")
    lines.append("")
    lines.append(f"💰 Toplam Sepet Tutarı: {total_basket_tl:.2f} TL")
    lines.append("")
    lines.append("💡 AI Özeti:")
    lines.append(ai_summary or "Özet alınamadı.")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
        text = text[: TELEGRAM_MAX_MESSAGE_LENGTH - 20] + "\n\n[...kısaltıldı]"
    return text


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ /start komutu – hoş geldin mesajı."""
    await update.message.reply_text(
        "Merhaba! Ben Maliyet Asistanı.\n\n"
        "Tek ürün: süt, tavuk göğüsü\n"
        "Sepet (virgülle ayırın): süt, yumurta, peynir\n\n"
        "En ucuz seçenekleri ve AI tavsiyesini göndereceğim."
    )


async def _run_basket_flow(
    update: Update,
    status_msg,
    queries: list[str],
) -> None:
    """Sepet modu: search_basket → process/filter per product → optimize_basket → format ve gönder."""
    raw_basket = await bot_manager.search_basket(queries)
    per_product_raw = raw_basket.get("per_product", {})
    per_product_processed: dict[str, list] = {}
    for q, data in per_product_raw.items():
        results = data.get("results", [])
        processed = data_processor.process(results)
        filtered = await filter_service.filter_and_rank(query=q, products=processed)
        per_product_processed[q] = filtered
    if not any(per_product_processed.values()):
        try:
            await status_msg.edit_text("Üzgünüm, sepetinizdeki ürünler için hiçbir markette sonuç bulunamadı.")
        except Exception:
            await update.message.reply_text("Üzgünüm, sepetinizdeki ürünler için hiçbir markette sonuç bulunamadı.")
        return
    basket_data = {"queries": queries, "per_product": per_product_processed}
    result = await ai_service.optimize_basket(basket_data)
    recommendations = result.get("recommendations", [])
    total_basket_tl = result.get("total_basket_tl", 0.0)
    summary = result.get("summary", "")
    text = _format_basket_result(queries, recommendations, total_basket_tl, summary)
    try:
        await status_msg.edit_text(text)
    except Exception as edit_err:
        logger.warning("[Telegram Bot] edit_text başarısız, reply deniyor: %s", edit_err)
        try:
            await update.message.reply_text(text)
        except Exception:
            await update.message.reply_text("Sepet sonucu alındı ancak gönderilemedi.")


async def handle_product_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ürün araması – BotManager ve AIService zincirini çalıştırır."""
    query = (update.message.text or "").strip()

    if len(query) < 2:
        await update.message.reply_text(
            "Lütfen aramak istediğiniz ürünü yazın (örn: 5 lt ayçiçek yağı)"
        )
        return

    if not bot_manager or not data_processor or not filter_service or not ai_service:
        hazir_olmayanlar = []
        if not bot_manager:
            hazir_olmayanlar.append("bot_manager")
        if not data_processor:
            hazir_olmayanlar.append("data_processor")
        if not filter_service:
            hazir_olmayanlar.append("filter_service")
        if not ai_service:
            hazir_olmayanlar.append("ai_service")
        logger.error(f"Hazır olmayan servis: {hazir_olmayanlar}")
        await update.message.reply_text("Servisler henüz hazır değil. Lütfen daha sonra tekrar deneyin.")
        return

    # Sepet modu: virgülle ayrılmış en az 2, en fazla 15 ürün
    if "," in query:
        queries = [q.strip() for q in query.split(",") if q.strip()]
        if 2 <= len(queries) <= 15:
            status_msg = None
            try:
                status_msg = await update.message.reply_text("🛒 Sepet için marketler taranıyor...")
            except Exception as e:
                logger.exception("[Telegram Bot] İlk mesaj gönderilemedi: %s", e)
                await update.message.reply_text("Bir teknik hata oluştu.")
                return
            try:
                await _run_basket_flow(update, status_msg, queries)
            except Exception as e:
                logger.exception("[Telegram Bot] Sepet hatası: %s", e)
                try:
                    if status_msg:
                        await status_msg.edit_text("Sepet işlenirken bir hata oluştu.")
                    else:
                        await update.message.reply_text("Sepet işlenirken bir hata oluştu.")
                except Exception:
                    await update.message.reply_text("Sepet işlenirken bir hata oluştu.")
            return

    status_msg = None
    try:
        status_msg = await update.message.reply_text("🔍 İsteğinizi aldım, marketleri taramaya başlıyorum...")
    except Exception as e:
        logger.exception("[Telegram Bot] İlk mesaj gönderilemedi: %s", e)
        await update.message.reply_text("Bir teknik hata oluştu.")
        return

    try:
        # 1. BotManager ile market taraması
        logger.info("[Telegram Bot] BotManager ile '%s' araması başladı...", query)
        raw = await bot_manager.search_all_markets(query)
        raw_count = len(raw.get("results", []))
        logger.info("[Telegram Bot] BotManager taraması tamamlandı: %d ürün bulundu", raw_count)

        # 2. DataProcessor ile birim fiyat işleme
        processed_products = data_processor.process(raw.get("results", []))
        logger.info("[Telegram Bot] DataProcessor: %d ürün işlendi", len(processed_products))

        # 3. FilterService ile filtreleme ve sıralama
        filtered_products = await filter_service.filter_and_rank(
            query=query,
            products=processed_products,
        )
        logger.info("[Telegram Bot] FilterService: %d ürün kaldı", len(filtered_products))

        # Hiç ürün bulunamadıysa
        if not filtered_products:
            try:
                await status_msg.edit_text("Üzgünüm, şu an hiçbir markette bu ürünü bulamadım.")
            except Exception:
                await update.message.reply_text("Üzgünüm, şu an hiçbir markette bu ürünü bulamadım.")
            return

        # 4. AIService ile tavsiye üretimi
        logger.info("[Telegram Bot] AI tavsiyesi alınıyor (processed_data: %d ürün)...", len(filtered_products))
        advice = await ai_service.generate_shopping_advice(
            user_query=query,
            processed_data=filtered_products,
        )

        # 5. En ucuz 3 + AI özeti formatla ve gönder
        result_text = _format_top3_and_advice(query, filtered_products, advice)
        try:
            await status_msg.edit_text(result_text)
        except Exception as edit_err:
            logger.warning("[Telegram Bot] edit_text başarısız, reply deniyor: %s", edit_err)
            try:
                await update.message.reply_text(result_text)
            except Exception as reply_err:
                logger.exception("[Telegram Bot] Cevap gönderilemedi: %s", reply_err)
                await update.message.reply_text("Sonuç alındı ancak gönderilemedi. Lütfen tekrar deneyin.")
    except Exception as e:
        logger.exception("[Telegram Bot] Arama hatası: %s", e)
        try:
            if status_msg:
                await status_msg.edit_text("Bir teknik hata oluştu.")
            else:
                await update.message.reply_text("Bir teknik hata oluştu.")
        except Exception:
            await update.message.reply_text("Bir teknik hata oluştu.")


async def error_handler(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Tüm unhandled exception'ları yakalar ve loglar.
    Production-ready error handling.
    """
    error = context.error
    
    if isinstance(error, Conflict):
        logger.error(
            "409 Conflict: Başka bir bot instance çalışıyor olabilir. "
            "Lock dosyasını kontrol edin: /tmp/telegram_bot.lock"
        )
        # Conflict durumunda botu durdurmayız, sadece loglarız
        return
    
    if isinstance(error, NetworkError):
        logger.warning("Network hatası (geçici olabilir): %s", error)
        return
    
    if isinstance(error, TelegramError):
        logger.error("Telegram API hatası: %s", error)
        return
    
    # Diğer hatalar
    logger.exception(
        "Unhandled exception in update handler: %s",
        error,
        exc_info=error,
    )
    
    # Kullanıcıya bilgi ver (eğer update varsa)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Üzgünüm, bir hata oluştu. Lütfen daha sonra tekrar deneyin."
            )
        except Exception:
            pass  # Mesaj gönderilemezse sessizce geç


async def post_init(application: Application) -> None:
    """Bot başladığında webhook temizle, Redis ve servisleri başlatır."""
    global bot_manager, data_processor, filter_service, ai_service
    
    # Webhook temizleme (409 Conflict önlemi)
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook silindi, asılı güncellemeler temizlendi (409 Conflict önlemi).")
    except Exception as e:
        logger.warning("Webhook silinirken hata (devam ediliyor): %s", e)
    
    # Redis bağlantısı (BotManager için gerekli)
    redis_client = None
    try:
        redis_client = await init_redis()
        await redis_client.ping()
        logger.info("Redis bağlantısı başarılı.")
    except Exception as e:
        err_msg = str(e).lower()
        logger.error(
            "Redis'e bağlanılamadı. BotManager çalışmayacak. "
            "Yerelde çalıştırıyorsanız: Docker konteynerlarının "
            "(postgres, redis) açık olduğundan emin olun. 'docker compose up -d redis' ile Redis'i başlatın. "
            "Hata: %s",
            e,
        )
        # Redis olmadan devam et (BotManager None kalacak)
    
    # BotManager başlatma (Redis gerekli)
    if redis_client:
        try:
            bot_manager = BotManager(redis_client)
            logger.info("BotManager başlatıldı.")
        except Exception as e:
            logger.error("BotManager başlatılamadı: %s", e)
            bot_manager = None
    else:
        logger.warning("Redis olmadığı için BotManager başlatılamadı.")
        bot_manager = None
    
    # DataProcessor başlatma (bağımsız)
    try:
        data_processor = DataProcessor()
        logger.info("DataProcessor başlatıldı.")
    except Exception as e:
        logger.error("DataProcessor başlatılamadı: %s", e)
        data_processor = None
    
    # AIService başlatma (API key gerekli ama başarısız olsa bile başlatılabilir)
    try:
        ai_service = AIService()
        # Model validation (async) - başarısız olsa bile servis çalışır
        try:
            await ai_service._ensure_model_validated()
            logger.info("AIService model validation başarılı.")
        except Exception as e:
            logger.warning("AIService model validation başarısız (servis yine de çalışacak): %s", e)
        logger.info("AIService başlatıldı.")
    except Exception as e:
        logger.error("AIService başlatılamadı: %s", e)
        ai_service = None
    
    # FilterService başlatma (AIService gerekli)
    if ai_service:
        try:
            filter_service = FilterService(ai_service)
            logger.info("FilterService başlatıldı.")
        except Exception as e:
            logger.error("FilterService başlatılamadı: %s", e)
            filter_service = None
    else:
        logger.warning("AIService olmadığı için FilterService başlatılamadı.")
        filter_service = None
    
    # Servis durumu özeti
    servis_durumu = {
        "bot_manager": bot_manager is not None,
        "data_processor": data_processor is not None,
        "ai_service": ai_service is not None,
        "filter_service": filter_service is not None,
    }
    logger.info("Telegram bot servisleri durumu: %s", servis_durumu)
    
    # En azından bazı servisler hazırsa devam et
    if not any(servis_durumu.values()):
        logger.error("Hiçbir servis başlatılamadı! Bot çalışmayacak.")
        raise RuntimeError("Hiçbir servis başlatılamadı.")


async def post_shutdown(application: Application) -> None:
    """Bot kapanırken Redis ve BotManager temizliği."""
    global bot_manager
    
    logger.info("Telegram bot kapatılıyor...")
    
    if bot_manager:
        try:
            await bot_manager.close()
        except Exception as e:
            logger.warning("BotManager kapatılırken hata: %s", e)
    
    try:
        await close_redis()
    except Exception as e:
        logger.warning("Redis kapatılırken hata: %s", e)
    
    logger.info("Telegram bot kapatıldı.")


async def main() -> None:
    """
    Async main function - production-ready lifecycle management.
    Uses application.initialize/start/stop instead of run_polling.
    """
    global application
    
    # Instance lock kontrolü
    if not _acquire_instance_lock():
        logger.error("Instance lock alınamadı, çıkılıyor.")
        sys.exit(1)
    
    # Signal handler'ları kur
    _setup_signal_handlers()
    
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN .env dosyasında tanımlı değil.")
        _release_instance_lock()
        sys.exit(1)
    
    try:
        # Application builder
        application = (
            ApplicationBuilder()
            .token(token)
            .read_timeout(30)
            .connect_timeout(30)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        
        # Handler'ları ekle
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_search))
        
        # Error handler ekle
        application.add_error_handler(error_handler)
        
        # Initialize
        logger.info("Telegram bot initialize ediliyor...")
        await application.initialize()

        # Manually call post_init to ensure services are started
        # Note: PTB's post_init callback may not fire reliably, so we call it explicitly
        try:
            await post_init(application)
        except Exception as e:
            logger.exception("post_init exception: %s", e)
            raise

        # Start
        logger.info("Telegram bot başlatılıyor (polling)...")
        await application.start()
        
        # Start updater (polling)
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        
        logger.info("Telegram bot çalışıyor. Durdurmak için Ctrl+C veya SIGTERM gönderin.")
        
        # Graceful shutdown için bekle
        await _shutdown_event.wait()
        
        logger.info("Shutdown sinyali alındı, bot kapatılıyor...")
        
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt alındı, bot kapatılıyor...")
    except Exception as e:
        logger.exception("Kritik hata: %s", e)
        raise
    finally:
        # Graceful shutdown
        try:
            if application:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
        except Exception as e:
            logger.warning("Shutdown sırasında hata: %s", e)
        
        # Lock serbest bırak
        _release_instance_lock()
        
        logger.info("Bot tamamen kapatıldı.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt ile sonlandırıldı.")
        sys.exit(0)
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)

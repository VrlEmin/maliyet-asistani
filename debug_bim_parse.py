"""
BİM Parse Debug Scripti

BİM botunun parse mantığını adım adım test eder.
"""

import asyncio
import httpx
from bs4 import BeautifulSoup
import re

async def debug_bim_parse():
    """BİM parse mantığını test et."""
    url = "https://www.bim.com.tr"
    
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    ) as client:
        print(f"📡 {url} çekiliyor...")
        response = await client.get(url)
        soup = BeautifulSoup(response.text, "lxml")
        
        print("\n" + "=" * 60)
        print("ÜRÜN KARTLARI (.product)")
        print("=" * 60)
        
        product_cards = soup.select(".product")
        print(f"Toplam kart sayısı: {len(product_cards)}")
        
        for i, card in enumerate(product_cards[:5], 1):
            print(f"\n{'='*60}")
            print(f"KART #{i}")
            print("=" * 60)
            
            # Ürün adı
            title_elem = card.select_one("h2.title")
            if title_elem:
                product_name = title_elem.get_text(strip=True)
                print(f"✓ Ürün Adı: {product_name}")
            else:
                print("✗ Ürün adı bulunamadı (h2.title)")
                # Alternatif seçiciler
                h2_all = card.select("h2")
                print(f"  Tüm H2 sayısı: {len(h2_all)}")
                for h2 in h2_all:
                    print(f"    - {h2.get_text(strip=True)[:80]} (classes: {h2.get('class', [])})")
            
            # Fiyat
            price_elem = card.select_one("span.curr")
            if price_elem:
                price_text_raw = price_elem.get_text(strip=True)
                price_text_parent = price_elem.parent.get_text(strip=True) if price_elem.parent else ""
                print(f"✓ Fiyat elementi bulundu:")
                print(f"  span.curr metni: '{price_text_raw}'")
                print(f"  Parent metni: '{price_text_parent}'")
                
                # Parse et
                price_text = price_text_parent or price_text_raw
                price_text = price_text.replace("₺", "").replace("TL", "").strip()
                
                if "." in price_text and "," in price_text:
                    price_parsed = price_text.replace(".", "").replace(",", ".")
                    try:
                        price_float = float(price_parsed)
                        print(f"  → Parse edilen fiyat: {price_float:.2f} TL")
                    except ValueError as e:
                        print(f"  ✗ Parse hatası: {e}")
                else:
                    print(f"  ⚠️  Beklenmeyen format: '{price_text}'")
            else:
                print("✗ Fiyat elementi bulunamadı (span.curr)")
                # Alternatif: tüm span'ları kontrol et
                spans = card.select("span")
                print(f"  Tüm span sayısı: {len(spans)}")
                for span in spans[:3]:
                    text = span.get_text(strip=True)
                    if "₺" in text or any(c.isdigit() for c in text):
                        print(f"    - {text[:80]} (classes: {span.get('class', [])})")
            
            # Gramaj
            card_text = card.get_text(" ", strip=True)
            gramaj_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:gr?|gram|g|kg)\b", card_text, re.I)
            if gramaj_match:
                gramaj_val = gramaj_match.group(1)
                print(f"✓ Gramaj bulundu: {gramaj_val}")
            else:
                print("✗ Gramaj bulunamadı")
            
            # Sorgu filtresi testi
            query = "çamaşır"
            query_words = [w for w in query.lower().split() if len(w) >= 3]
            if title_elem:
                name_lower = product_name.lower()
                matches = [w for w in query_words if w in name_lower]
                if matches:
                    print(f"✓ Sorgu filtresi geçti: '{query}' → eşleşen kelimeler: {matches}")
                else:
                    print(f"✗ Sorgu filtresi geçmedi: '{query}' → ürün adında '{query_words}' yok")
            
            # Card HTML önizleme
            print(f"\nCard HTML (ilk 300 karakter):")
            print(str(card)[:300] + "...")

if __name__ == "__main__":
    asyncio.run(debug_bim_parse())

"""
BİM HTML Debug Scripti

BİM'in ana sayfasından gelen HTML'i analiz eder.
"""

import asyncio
import httpx
from bs4 import BeautifulSoup

async def debug_bim_html():
    """BİM ana sayfasını çek ve HTML yapısını analiz et."""
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
        print(f"✓ Status: {response.status_code}")
        print(f"✓ Content Length: {len(response.text)} bytes")
        print()
        
        soup = BeautifulSoup(response.text, "lxml")
        
        # Ürün kartlarını bulmaya çalış
        print("=" * 60)
        print("ÜRÜN KARTI SEÇİCİLERİ TEST EDİLİYOR")
        print("=" * 60)
        
        selectors = [
            ".product",
            ".urun",
            "[class*='aktuel']",
            "[class*='product']",
            "article",
            "[class*='card']",
            "[class*='item']",
        ]
        
        for selector in selectors:
            elements = soup.select(selector)
            if elements:
                print(f"✓ '{selector}': {len(elements)} element bulundu")
                if len(elements) > 0:
                    # İlk elementin yapısını göster
                    first = elements[0]
                    print(f"  İlk element örneği:")
                    print(f"    Tag: {first.name}")
                    print(f"    Classes: {first.get('class', [])}")
                    text_preview = first.get_text(strip=True)[:100]
                    print(f"    Text preview: {text_preview}...")
                    print()
        
        # Fiyat içeren elementleri bul
        print("=" * 60)
        print("FİYAT İÇEREN ELEMENTLER")
        print("=" * 60)
        
        # ₺ sembolü içeren elementler
        price_elements = soup.find_all(string=lambda text: text and '₺' in text)
        print(f"₺ içeren metin sayısı: {len(price_elements)}")
        
        if price_elements:
            print("\nİlk 5 fiyat örneği:")
            for i, elem in enumerate(price_elements[:5], 1):
                parent = elem.parent
                print(f"\n{i}. Fiyat metni: {elem.strip()}")
                print(f"   Parent tag: {parent.name if parent else 'None'}")
                print(f"   Parent classes: {parent.get('class', []) if parent else []}")
                # Üst context'i göster
                grandparent = parent.parent if parent else None
                if grandparent:
                    gp_text = grandparent.get_text(strip=True)[:150]
                    print(f"   Context: {gp_text}...")
        
        # H2 başlıklarını kontrol et
        print("\n" + "=" * 60)
        print("H2 BAŞLIKLARI (Ürün adları için)")
        print("=" * 60)
        
        h2_elements = soup.find_all("h2")
        print(f"Toplam H2 sayısı: {len(h2_elements)}")
        
        if h2_elements:
            print("\nİlk 10 H2:")
            for i, h2 in enumerate(h2_elements[:10], 1):
                text = h2.get_text(strip=True)
                classes = h2.get('class', [])
                print(f"{i}. {text[:80]}... (classes: {classes})")
        
        # Sayfanın bir kısmını kaydet
        print("\n" + "=" * 60)
        print("HTML ÖNİZLEME (İlk 2000 karakter)")
        print("=" * 60)
        print(response.text[:2000])
        print("...")

if __name__ == "__main__":
    asyncio.run(debug_bim_html())

# BİM Mobil API Aktif Etme Rehberi

## Mevcut Durum

Mobil API şu an **devre dışı** durumda. Kod hazır ama gerçek endpoint'ler ve header'lar tespit edilmesi gerekiyor.

**Dosya:** `backend/scrapers/bim_bot.py`
- Satır 103-107: Mobil API çağrısı yorum satırında
- Satır 32-34: Endpoint'ler placeholder
- Satır 49-56: Header'lar placeholder

## Ne Zaman Aktif Olacak?

Mobil API, **network trafiği analizi** tamamlandıktan sonra aktif edilecek. Bu analiz şunları tespit edecek:

1. ✅ Gerçek API endpoint URL'leri
2. ✅ HTTP method (GET/POST)
3. ✅ Request header'ları (X-Device-Id, Authorization, User-Agent, vb.)
4. ✅ Request body formatı (JSON parametreleri)
5. ✅ Response formatı (JSON yapısı)

## Nasıl Aktif Edilecek?

### Adım 1: Network Trafiği Analizi

**Gereksinimler:**
- Android/iOS cihaz
- BİM mobil uygulaması kurulu
- Proxy tool (mitmproxy, Charles Proxy, veya Fiddler)

**Adımlar:**

1. **Proxy Kurulumu:**
   ```bash
   # mitmproxy kurulumu (Linux/Mac)
   pip install mitmproxy
   
   # Proxy'yi başlat
   mitmproxy -p 8080
   ```

2. **Cihazı Proxy'ye Bağla:**
   - Android: WiFi ayarları → Proxy → Manuel → IP: [Bilgisayar IP], Port: 8080
   - iOS: WiFi ayarları → Proxy → Manuel → IP: [Bilgisayar IP], Port: 8080
   - mitmproxy sertifikasını cihaza yükle (mitm.it)

3. **BİM Uygulamasında Arama Yap:**
   - BİM mobil uygulamasını aç
   - "süt" veya başka bir ürün ara
   - Network trafiğini izle

4. **API İsteklerini Tespit Et:**
   - mitmproxy'de `/api/`, `/rest/`, `/v1/` gibi endpoint'leri ara
   - Ürün arama isteğini bul
   - Request detaylarını kaydet:
     - URL (base + endpoint)
     - Method (GET/POST)
     - Headers (X-Device-Id, Authorization, User-Agent, vb.)
     - Request body (JSON parametreleri)
     - Response formatı (JSON yapısı)

### Adım 2: Kod Güncellemesi

Tespit edilen bilgilerle `bim_bot.py` dosyasını güncelle:

**1. Endpoint'leri Güncelle (Satır 32-34):**
```python
# ÖNCE (Placeholder):
BIM_API_BASE = "https://api.bim.com.tr"
BIM_MOBILE_SEARCH_URL = f"{BIM_API_BASE}/v1/products/search"

# SONRA (Gerçek endpoint):
BIM_API_BASE = "https://api.bim.com.tr"  # veya gerçek base URL
BIM_MOBILE_SEARCH_URL = f"{BIM_API_BASE}/v1/products/search"  # gerçek endpoint
```

**2. Header'ları Güncelle (Satır 49-56):**
```python
# ÖNCE (Placeholder):
BIM_MOBILE_HEADERS = {
    "X-Device-Id": str(uuid.uuid4()),  # Placeholder
    "Authorization": "Bearer ...",  # Placeholder
    ...
}

# SONRA (Gerçek header'lar):
BIM_MOBILE_HEADERS = {
    "X-Device-Id": "gerçek-device-id",  # Gerçek device ID
    "Authorization": "Bearer gerçek-token",  # Gerçek token (varsa)
    "User-Agent": "BIM-Online/2.1.5 (Android 13)",  # Gerçek User-Agent
    ...
}
```

**3. Request Formatını Güncelle (Satır 136-143):**
```python
# ÖNCE (Tahmini):
response = await self._make_request(
    BIM_MOBILE_SEARCH_URL,
    method="POST",  # veya GET
    json_body={
        "query": query,
        "page": 1,
        "limit": 20,
    },
    headers=BIM_MOBILE_HEADERS,
)

# SONRA (Gerçek format):
response = await self._make_request(
    BIM_MOBILE_SEARCH_URL,
    method="POST",  # veya GET - gerçek method
    json_body={
        "search": query,  # veya gerçek parametre adı
        "page": 1,
        "size": 20,  # veya gerçek parametre adı
    },
    headers=BIM_MOBILE_HEADERS,
)
```

**4. Response Parse'ı Güncelle (Satır 148-170):**
```python
# Gerçek response formatına göre güncelle:
data = response.json()
products = data.get("products", [])  # veya gerçek path
# veya
products = data.get("data", {}).get("items", [])  # gerçek yapıya göre
```

**5. Mobil API'yi Aktif Et (Satır 103-107):**
```python
# ÖNCE (Yorum satırında):
# results = await self._search_mobile_api(query)
# if results:
#     await self._set_cache(cache_key, results)
#     return results

# SONRA (Aktif):
results = await self._search_mobile_api(query)
if results:
    await self._set_cache(cache_key, results)
    return results
```

### Adım 3: Test ve Doğrulama

```bash
# Cache'i temizle
docker exec maliyet-asistani-cache redis-cli FLUSHALL

# Botu test et
docker exec maliyet-asistani-api python test_bim_bot.py süt

# Logları kontrol et
docker logs maliyet-asistani-api | grep -i "bim.*mobil"
```

## Örnek Network Trafiği Analizi Çıktısı

```
[REQUEST]
POST https://api.bim.com.tr/v1/products/search
Headers:
  X-Device-Id: 550e8400-e29b-41d4-a716-446655440000
  Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
  User-Agent: BIM-Online/2.1.5 (Android 13; SM-G991B)
  Content-Type: application/json
Body:
  {
    "searchTerm": "süt",
    "page": 1,
    "pageSize": 20
  }

[RESPONSE]
Status: 200 OK
Body:
  {
    "success": true,
    "data": {
      "products": [
        {
          "id": "12345",
          "name": "Süt 1L",
          "price": 1995,  // kuruş
          "currency": "TRY",
          "imageUrl": "https://...",
          "stock": true,
          "weight": "1000g"
        }
      ]
    }
  }
```

## Alternatif Yöntemler

### 1. GitHub Scriptlerini İnceleme
- BimAktuelGetter ve pyBim scriptlerini detaylı incele
- Ancak bunlar sadece aktüel ürünler için çalışıyor, stok erişimi yok

### 2. APK Reverse Engineering
- BİM APK'sını decompile et
- API endpoint'lerini ve header'ları kod içinden çıkar
- Daha teknik ve zaman alıcı

### 3. Resmi API Dokümantasyonu
- BİM'in resmi API dokümantasyonu varsa kullan
- Şu an için resmi API dokümantasyonu bulunamadı

## Önemli Notlar

⚠️ **Yasal Uyarı:** API reverse engineering yasal sorunlara yol açabilir. BİM'in resmi API'si varsa tercih edilmeli.

⚠️ **Rate Limiting:** Mobil API'ler rate limiting kullanabilir. Dikkatli olunmalı.

⚠️ **Authentication:** Token'lar süreli olabilir. Token yenileme mekanizması gerekebilir.

⚠️ **API Değişiklikleri:** Mobil API'ler sık sık değişebilir. Kod güncellemeleri gerekebilir.

## Sonraki Adımlar

1. Network trafiği analizi yapılması gerekiyor
2. Tespit edilen bilgilerle kod güncellenecek
3. Test ve doğrulama yapılacak
4. Production'a deploy edilecek

## İletişim

Network trafiği analizi tamamlandığında, tespit edilen bilgileri paylaşın ve kod güncellemesi yapılacak.

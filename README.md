# Komisyon Arama

Eğitim Fakültesi Komisyonlar sayfasındaki PDF'lerden üye tablolarını her gece okur ve ad soyadla aranabilir bir sayfa yayımlar.

- `scripts/guncelle.py`: PDF'leri okur, `docs/veri.json` dosyasını üretir.
- `.github/workflows/guncelle.yml`: her gece 04:17'de ve elle başlatıldığında çalışır.
- `docs/index.html`: arama sayfası.

Çalışma başarısız olursa eski veri korunur ve GitHub hesap sahibine e-posta gönderir. Komisyonlar sayfasının adresi `scripts/guncelle.py` içindeki `SAYFA` satırındadır.

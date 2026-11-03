#!/usr/bin/env python3
"""Komisyonlar sayfasindaki PDF'leri okuyup docs/veri.json dosyasini uretir.
Hata durumunda mevcut veriyi bozmaz: sayfa okunamazsa veya hic uye bulunamazsa
kod 1 ile cikar, GitHub de size e-posta gonderir."""
import datetime, html, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request

SAYFA = "https://www.agri.edu.tr/detail.aspx?id=1454&bid=2&tid=7&dil=tr-TR"
CIKTI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "veri.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; KomisyonArama/1.0)"}
# Unvan parcasi nokta ya da bosluk ile bitmeli; yoksa "Görkem" gibi adlar unvan sanilir.
UNVAN = re.compile(r"^(?:(?:Prof|Doç|Doc|Dr|Arş|Öğr|Gör|Üyesi|Öğretim|Görevlisi|Araştırma)(?:\.\s*|\s+))+", re.I)
# Tek basina "Öğr." ogrenci kisaltmasi da olabilir; akademik unvan sayilmaz.
AKADEMIK = re.compile(r"Prof|Doç|Doc|Dr|Gör|Üyesi", re.I)
# Unvani olmadigi halde aranabilir olacak kisiler (fakulte sekreteri).
ISTISNA = {"bayram demir"}
BITIS = re.compile(r"BİRİNCİ\s+BÖLÜM|^\s*Amaç\b|^\s*Madde\s+\d", re.M)
BASLIK = re.compile(r"Ad[ıi]\s*[-/]?\s*Soyad[ıi]\b[^\n]*\n", re.I)

TEK_SATIR = re.compile(r"^(.*[A-ZÇĞİÖŞÜ]{2,})(?:\s+(.*[a-zçğıöşü].*))?$")

def norm(s):
    s = s.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    for a, b in zip("çğöşüâîû", "cgosuaiu"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()

def uyeler(metin):
    """Yalnizca 'Adi Soyadi / Gorevi' tablosunu okur; tablo yoksa None dondurur."""
    # PDF'lerdeki bilinen yazim hatalari (depo sahibince dogrulandi).
    metin = re.sub(r"Zehra\s+CEHVER", "Zehra CEVHER", metin, flags=re.I)
    # Baslik bazen iki satira bolunur ("Adi Soyadi" ayri, "Gorevi" ayri).
    m = BASLIK.search(metin) or re.search(r"^\s*Görevi\s*$\n?", metin, re.M | re.I)
    if not m:
        return None
    govde = metin[m.end():]
    b = BITIS.search(govde)
    if b:
        govde = govde[:b.start()]
    sonuc = []
    for satir in govde.splitlines():
        sutun = [p.strip() for p in re.split(r"\s{2,}", satir.strip()) if p.strip()]
        if len(sutun) == 1 and UNVAN.match(sutun[0]):
            # Tek bosluklu satir: ad, BUYUK HARFLI soyadla biter, kalani gorevdir.
            t = TEK_SATIR.match(sutun[0])
            sutun = [t.group(1), (t.group(2) or "Görev belirtilmemiş").strip()] if t else sutun
        if len(sutun) < 2:
            continue
        ad_tam, gorev = sutun[0], sutun[-1]
        ad = UNVAN.sub("", ad_tam).strip()
        if len(ad.split()) >= 2 and not re.search(r"\d", ad):
            sonuc.append({"ad_tam": ad_tam, "ad": ad, "gorev": gorev})
    return sonuc or None

def aranabilir(u):
    """Yalnizca akademik unvanli kisiler ve ISTISNA listesi yayimlanir."""
    on_ek = u["ad_tam"][:len(u["ad_tam"]) - len(u["ad"])]
    return bool(AKADEMIK.search(on_ek)) or norm(u["ad"]) in ISTISNA

def unvan_derecesi(ad_tam):
    """Ayni kisi belgelerde farkli unvanla gecerse en yuksegini gostermek icin."""
    for derece, kalip in ((5, r"Prof"), (4, r"Do[çc]"), (3, r"\bDr\b"), (2, r"Öğr"), (1, r"Arş")):
        if re.search(kalip, ad_tam, re.I):
            return derece
    return 0

def tani(metin):
    """Okunamayan belgenin yapisini loga yazar. Log herkese acik oldugu icin
    unvansiz satirlar ve adlar maskelenir; yalnizca yapi gorunur."""
    if not metin or not metin.strip():
        return "  metin yok (taranmis belge olabilir)"
    sonuc = [f"  {len(metin)} karakter"]
    for satir in [x for x in metin.splitlines() if x.strip()][:40]:
        duz = satir.strip()
        anahtar = re.findall(r"Ad[ıi]\s*[-/]?\s*Soyad[ıi]|Ad\s*Soyad|Görev\w*|[UÜ]nvan\w*|Sıra|S\.?No|Komisyon\w*", duz, re.I)
        if anahtar:  # satirin kendisi degil, yalnizca anahtar sozcukler yazilir
            sonuc.append("  A: " + ",".join(anahtar) + " sutun=" + str(len(re.split(r"\s{2,}", duz))))
        elif UNVAN.match(duz):
            sonuc.append("  U: " + UNVAN.match(duz).group(0) + "*** sutun=" + str(len(re.split(r"\s{2,}", duz))))
        else:
            sonuc.append("  -: *** sutun=" + str(len(re.split(r"\s{2,}", duz))))
    return "\n".join(sonuc)

def pdf_metni(yol):
    return subprocess.run(["pdftotext", "-layout", yol, "-"], check=True,
                          capture_output=True, text=True).stdout

def indir(url):
    istek = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(istek, timeout=60) as y:
        return y.read()

def baglantilar(sayfa_html):
    bulunan, gorulen = [], set()
    for href, ic in re.findall(r'<a\b[^>]*href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>', sayfa_html, re.S | re.I):
        tam = urllib.parse.urljoin(SAYFA, html.unescape(href).strip())
        # Dosya adlarinda bosluk ve Turkce harf var; indirmeden once kodlanmali.
        tam = urllib.parse.quote(tam, safe=":/?&=%#+,;@!$'()*")
        if "komisyonlar" not in norm(urllib.parse.unquote(tam)) or tam in gorulen:
            continue
        gorulen.add(tam)
        ad = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", ic)))
        ad = re.sub(r"\bPDF\b|Belgeyi görüntüle|›", "", ad).strip()
        if not ad or len(ad) > 80:  # bos ya da aciklama metni iceren baglanti
            ad = os.path.splitext(os.path.basename(urllib.parse.unquote(tam)))[0].title()
        bulunan.append((ad, tam))
    return bulunan

def veri_olustur(belgeler, simdi):
    """belgeler: (komisyon adi, url, metin ya da None)"""
    kisiler, okunamayan, okunan = {}, [], 0
    for ad, url, metin in belgeler:
        liste = uyeler(metin) if metin else None
        if not liste:
            okunamayan.append({"komisyon": ad, "url": url})
            continue
        okunan += 1
        for u in filter(aranabilir, liste):
            k = kisiler.setdefault(norm(u["ad"]), {"ad_tam": u["ad_tam"], "komisyonlar": []})
            if unvan_derecesi(u["ad_tam"]) > unvan_derecesi(k["ad_tam"]):
                k["ad_tam"] = u["ad_tam"]
            k["komisyonlar"].append({"komisyon": ad, "gorev": u["gorev"], "url": url})
    return {"son_kontrol": simdi, "kaynak": SAYFA, "belge_sayisi": len(belgeler),
            "okunan_belge": okunan, "okunamayan": okunamayan,
            "kisiler": [dict(anahtar=a, **kisiler[a]) for a in sorted(kisiler)]}

def main():
    simdi = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    try:
        sayfa = indir(SAYFA).decode("utf-8", "replace")
    except Exception as e:
        sys.exit(f"HATA: Komisyonlar sayfasi okunamadi: {e}")
    linkler = baglantilar(sayfa)
    if not linkler:
        sys.exit("HATA: Sayfada komisyon PDF'i bulunamadi; mevcut veri korundu.")
    belgeler, ham = [], {}
    with tempfile.TemporaryDirectory() as d:
        for ad, url in linkler:
            metin = None
            try:
                yol = os.path.join(d, "b.pdf")
                with open(yol, "wb") as f:
                    f.write(indir(url))
                metin = pdf_metni(yol)
                ham[url] = subprocess.run(["pdftotext", yol, "-"], capture_output=True, text=True).stdout
            except Exception as e:
                print(f"UYARI: {ad}: {e}")
            belgeler.append((ad, url, metin))
            time.sleep(0.5)
    veri = veri_olustur(belgeler, simdi)
    metinler = {url: metin for _, url, metin in belgeler}
    if not veri["kisiler"]:
        sys.exit("HATA: Hicbir belgede uye tablosu okunamadi; mevcut veri korundu.")
    with open(CIKTI, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)
    print(f"{veri['okunan_belge']}/{veri['belge_sayisi']} belge okundu, {len(veri['kisiler'])} kisi.")
    for o in veri["okunamayan"]:
        print("OKUNAMADI:", o["komisyon"])
        print(tani(metinler.get(o["url"])))
        print("  -- duz metin --")
        print(tani(ham.get(o["url"])))

if __name__ == "__main__":
    main()

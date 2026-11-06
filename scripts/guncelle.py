#!/usr/bin/env python3
"""Komisyonlar sayfasindaki PDF'leri okuyup docs/veri.json dosyasini uretir.
Hata durumunda mevcut veriyi bozmaz: sayfa okunamazsa veya hic uye bulunamazsa
kod 1 ile cikar, GitHub de size e-posta gonderir."""
import datetime, hashlib, html, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request

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

BIRIM_SATIR = re.compile(r"^\d+\s+(.+?)\s*(\(Başkan\))?$")
YALNIZ_GOREV = re.compile(r"Üye|Başkan|Raportör|Sekreter", re.I)
TEK_SATIR = re.compile(r"^(.*[A-ZÇĞİÖŞÜ]{2,})(?:\s+(.*[a-zçğıöşü].*))?$")

def norm(s):
    s = s.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    for a, b in zip("çğöşüâîû", "cgosuaiu"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()

# Metin katmani bozuk PDF'ler: adlarin bir kismi metin olarak okunamiyor. Tablo belgenin
# goruntusunden elle aktarildi. Yalnizca ayni dosya (ayni SHA-256) icin gecerlidir; PDF
# degisirse normal okumaya donulur ve logda uyari cikar.
ELLE = {
    "fc7e5652e8e90efa823e7c00e445327075df32e45a6fbb2917c9d099de7f48c4": """Adı Soyadı   Görevi
Prof. Dr. Yusuf ÇETİN   Süreç Yöneticisi
Prof. Dr. Evrim TOKLU   Sorumlu Dekan Yardımcısı
Bayram DEMİR   Kalite Sorumlusu
Prof. Dr. Mehmet YALÇIN   Bölüm Kalite Sorumlusu
Prof. Dr. Murat ÇALIŞOĞLU   Bölüm Kalite Sorumlusu
Prof. Dr. İhsan Nuri DEMİREL   Bölüm Kalite Sorumlusu
Prof. Dr. Yasin KILIÇ   Bölüm Kalite Sorumlusu
Prof. Dr. Fatih KARİP   Bölüm Kalite Sorumlusu
Prof. Dr. Aydın KIZILASLAN   Bölüm Kalite Sorumlusu
Dr. Öğr. Üyesi Betül ATAY   Bölüm Kalite Sorumlusu
Prof. Dr. Mustafa GÜRBÜZ   Üye
Doç. Dr. Mehmet Fatih ÖZCAN   Üye
Doç. Dr. Burcu Seher ÇALIKOĞLU   Üye
Doç. Dr. Samet ATA   Üye
Doç. Dr. Hüseyin BAYRAM   Üye
Doç. Dr. Alican KAYA   Üye
Dr. Öğr. Üyesi Anıl KANDEMİR   Üye
Dr. Öğr. Üyesi Halil ZEHİR   Üye
Dr. Öğr. Üyesi Asuman UÇAR   Üye
Dr. Öğr. Üyesi Büşra KOÇAK MACUN   Üye
Dr. Öğr. Üyesi Betül ATAY   Üye
Dr. Öğr. Üyesi Ahmet HARMANCI   Üye
Dr. Öğr. Üyesi Büşra İnci GÜMÜŞ   Üye
""" }

def eksik_satir(metin):
    """Adi okunamamis tablo satirlarini sayar: yalniz unvan ya da yalniz gorev kalan satir."""
    m = BASLIK.search(metin) or re.search(r"^\s*Görevi\s*$\n?", metin, re.M | re.I)
    if not m:
        return 0
    govde = metin[m.end():]
    b = BITIS.search(govde)
    govde = govde[:b.start()] if b else govde
    say = gorevsiz_ad = 0
    for satir in govde.splitlines():
        parca = re.split(r"\s{2,}", satir.strip())
        ilk = parca[0]
        if ilk and UNVAN.sub("", ilk).strip() == "":
            say += 1
        elif satir[:30].strip() == "" and re.search(r"Üye|Başkan|Sorumlu|Yönetici", satir):
            say += 1
        elif len(parca) == 1 and UNVAN.match(ilk):
            gorevsiz_ad += 1  # bu ad, kayik gorev satiriyla eslesir; eksik sayilmaz
    return max(0, say - gorevsiz_ad)

def duzelt(metin):
    """PDF'lerdeki bilinen yazim hatalari (depo sahibince dogrulandi)."""
    metin = re.sub(r"\bK(?:adriye|\.)\s*Funda\s+NAY[İI]R\s*EK[İI]Z", "Kadriye Funda NAYİREKİZ", metin, flags=re.I)
    metin = re.sub(r"Zehra\s+CEHVER", "Zehra CEVHER", metin, flags=re.I)
    metin = re.sub(r"Muhammet\s+Celal\s+URAS", "Muhammed Celal URAS", metin, flags=re.I)
    return re.sub(r"Serkan\s+BOZAN", "Serhan BOZAN", metin, flags=re.I)

def tr_baslik(s):
    """BUYUK HARFLI komisyon basligini okunur hale getirir."""
    kucuk = lambda w: w.replace("I", "ı").replace("İ", "i").lower()
    sonuc = []
    for w in s.split():
        if w.strip("()") == "PDR":
            sonuc.append(w)
        elif w == "VE":
            sonuc.append("ve")
        else:
            i = 1 if w[0] == "(" else 0
            sonuc.append(w[:i + 1] + kucuk(w[i + 1:]))
    return " ".join(sonuc)

def birim_bolumleri(metin):
    """Birim Bazli belge: BUYUK HARFLI baslik, altinda '1  Unvan Ad SOYAD (Baskan)' satirlari."""
    bolumler, bekleyen, gecerli = [], [], None
    for satir in duzelt(metin).splitlines():
        duz = satir.strip()
        if not duz:
            continue
        m = BIRIM_SATIR.match(duz)
        if m:
            if bekleyen:
                baslik = " ".join(bekleyen)
                # Belgede bir baslikta "AKADEMIK TESVIK KOMISYONU" iki kez yazilmis.
                baslik = re.sub(r"(\b[^()]+? KOMİSYONU) \1$", r"\1", baslik)
                gecerli = []
                bolumler.append((tr_baslik(baslik), gecerli))
                bekleyen = []
            if gecerli is None:
                continue
            ad_tam = m.group(1).strip()
            ad = UNVAN.sub("", ad_tam).strip()
            if len(ad.split()) >= 2:
                gecerli.append({"ad_tam": ad_tam, "ad": ad, "gorev": "Başkan" if m.group(2) else "Üye"})
        elif duz == duz.upper() and not duz.endswith("KOMİSYONLAR"):
            bekleyen.append(duz)
    return [b for b in bolumler if b[1]]

def uyeler(metin):
    """Yalnizca 'Adi Soyadi / Gorevi' tablosunu okur; tablo yoksa None dondurur."""
    metin = duzelt(metin)
    # Baslik bazen iki satira bolunur ("Adi Soyadi" ayri, "Gorevi" ayri).
    m = BASLIK.search(metin) or re.search(r"^\s*Görevi\s*$\n?", metin, re.M | re.I)
    if not m:
        return None
    govde = metin[m.end():]
    b = BITIS.search(govde)
    if b:
        govde = govde[:b.start()]
    sonuc, bekleyen_gorev, gorevsiz = [], None, None
    for satir in govde.splitlines():
        sutun = [p.strip() for p in re.split(r"\s{2,}", satir.strip()) if p.strip()]
        # Gorev hucresi dikeyde kayik olabilir: "Uye" adin bir satir ustunde ya da altinda kalir.
        if len(sutun) == 1 and YALNIZ_GOREV.fullmatch(sutun[0]):
            if gorevsiz:
                gorevsiz["gorev"], gorevsiz = sutun[0], None
            else:
                bekleyen_gorev = sutun[0]
            continue
        if len(sutun) == 1 and UNVAN.match(sutun[0]):
            # Tek bosluklu satir: ad, BUYUK HARFLI soyadla biter, kalani gorevdir.
            t = TEK_SATIR.match(sutun[0])
            sutun = [t.group(1), (t.group(2) or "Görev belirtilmemiş").strip()] if t else sutun
        if len(sutun) < 2:
            continue
        ad_tam, gorev = sutun[0], sutun[-1]
        ad = UNVAN.sub("", ad_tam).strip()
        if len(ad.split()) >= 2 and not re.search(r"\d", ad):
            u = {"ad_tam": ad_tam, "ad": ad, "gorev": gorev}
            sonuc.append(u)
            if gorev == "Görev belirtilmemiş":
                if bekleyen_gorev:
                    u["gorev"], bekleyen_gorev = bekleyen_gorev, None
                else:
                    gorevsiz = u
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

def guvenli_url(url):
    parca = urllib.parse.urlsplit(url)
    yol = urllib.parse.quote(
        parca.path, safe="/%"
    )
    return urllib.parse.urlunsplit((
        parca.scheme,
        parca.netloc,
        yol,
        parca.query,
        parca.fragment
    ))

  

def indir(url):
    istek = urllib.request.Request(guvenli_url(url), headers=UA)
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
        bolumler = [(ad, liste)] if liste else (birim_bolumleri(metin) if metin else [])
        if not bolumler:
            okunamayan.append({"komisyon": ad, "url": url})
            continue
        okunan += 1
        for bolum, satirlar in bolumler:
            for u in filter(aranabilir, satirlar):
                k = kisiler.setdefault(norm(u["ad"]), {"ad_tam": u["ad_tam"], "komisyonlar": []})
                if unvan_derecesi(u["ad_tam"]) > unvan_derecesi(k["ad_tam"]):
                    k["ad_tam"] = u["ad_tam"]
                k["komisyonlar"].append({"komisyon": bolum, "gorev": u["gorev"], "url": url})
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
                with open(yol, "rb") as bf:
                    ozet = hashlib.sha256(bf.read()).hexdigest()
                if ozet in ELLE:
                    metin = ELLE[ozet]
                elif eksik_satir(metin):
                    print(f"UYARI: {ad}: {eksik_satir(metin)} satirda ad okunamadi; liste eksik olabilir.")
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Los Ojos Trendyol Satis Takip - Veri Toplayici (collector)
-----------------------------------------------------------
Bu program Trendyol'daki Los Ojos markasinin tum urunlerini gezer,
her urun icin Trendyol'un gosterdigi sosyal kanit verilerini toplar:
  - orderCountL3D : "son 3 gunde X+ satti" (Trendyol'un kendi satis rakami)
  - basketCount   : kac kisinin sepetinde
  - favoriteCount : favori sayisi
  - pageViewCount : goruntulenme
  - ratingCount   : yorum / degerlendirme sayisi (satis hizinin en saglam isareti)

Calistiginda data/history.json dosyasina o gunun bir "fotografini" ekler.
Gun gectikce bu fotograflarin farki = satis hizi tahmini.

Hicbir giris / sifre / cerez gerekmez. Sadece internet baglantisi.
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.parse

BRAND_ID = 147875
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
REQUEST_PAUSE = 0.8
MAX_PAGES = 40
# Sadece SATAN urunleri takip et: katalogu "cok satanlar" (BEST_SELLER) sirasiyla
# cek ve yalnizca ilk TOP_SELLER_PAGES sayfayi al (~24 urun/sayfa). Satan urunler
# basta gelir, olu stok en sonda kalir ve hic cekilmez. Boylece istek sayisi
# sabit ve dusuk kalir (residential proxy pahali; her istek 10-25 kredi) -> her
# gun toplama bedava kotalara sigar. Daha fazla/az urun icin bu sayiyi degistir.
# Ortam degiskeniyle asilabilir: Turkiye-PC kurulumunda istekler bedava oldugu
# icin TOP_SELLER_PAGES=10 verilip tam katalog cekilir (radar daha isabetli).
TOP_SELLER_PAGES = int(os.environ.get("TOP_SELLER_PAGES", "4"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SEARCH_API = ("https://apigw.trendyol.com/discovery-web-searchgw-service/"
              "v2/api/infinite-scroll/sr")
SOCIAL_API = ("https://apigw.trendyol.com/discovery-sfint-search-service/"
              "api/social-proof/")


def _load_env_file():
    """.env dosyasi varsa (yerel calisirken) icindeki degiskenleri yukler.
    Repo genel/public oldugu icin token asla koda/commite yazilmaz."""
    path = os.path.join(DATA_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env_file()
# Trendyol Turkiye disini engelledigi icin istekler Turkiye cikisli bir proxy
# servisi uzerinden yapilir. Oncelik SCRAPFLY (datacenter+ASP ile istek basina
# 1 kredi, 1000 kredi bedava); yoksa SCRAPEDO (ucretsizde residential=10 kredi).
SCRAPFLY_TOKEN = os.environ.get("SCRAPFLY_TOKEN")
SCRAPEDO_TOKEN = os.environ.get("SCRAPEDO_TOKEN")
# Cloudflare ilk istekte bir gecis cerezi (cf_clearance) veriyor; sonraki
# sayfalar bu cerez/IP olmadan 403 aliyor. Scrapfly session'i ayni IP + cerez
# havuzunu koruyarak sayfalamayi calistirir. Her calisma icin ayri session adi.
_SCRAPFLY_SESSION = "losojos" + str(int(time.time()))


def _using_proxy():
    return bool(SCRAPFLY_TOKEN or SCRAPEDO_TOKEN)


def _scrapfly_fetch(target):
    """Scrapfly ile Turkiye cikisli istek; icerigi (result.content) doner.

    Datacenter proxy (1 kredi) denendi ama Trendyol'un Cloudflare'i bu IP'leri
    engelliyor (test: 0/5, render_js ile 1/3). Bu yuzden dogrudan guvenilir
    residential proxy (25 kredi) kullaniliyor. Cloudflare ilk istekte cf_clearance
    cerezi veriyor; sticky session ayni IP+cerezi sonraki sayfalarda koruyarak
    sayfalamayi calistirir. Sticky IP takilirsa bir kez yeni session ile denenir."""
    global _SCRAPFLY_SESSION

    def _try(session):
        u = (f"https://api.scrapfly.io/scrape?key={SCRAPFLY_TOKEN}"
             f"&url={target}&country=tr&proxy_pool=public_residential_pool&asp=true"
             f"&session={session}&session_sticky_proxy=true")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(u, headers={"Accept": "application/json"}),
                    timeout=150) as r:
                env = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return None
        res = env.get("result") or {}
        content = res.get("content", "") or ""
        return content if (res.get("status_code") == 200 and content) else None

    c = _try(_SCRAPFLY_SESSION)
    if c is not None:
        return c
    _SCRAPFLY_SESSION = "losojos" + str(int(time.time() * 1000))
    return _try(_SCRAPFLY_SESSION) or ""


def _get(url, validate=None):
    """Trendyol Turkiye disindan engelledigi icin istekler Turkiye cikisli bir
    proxy servisi uzerinden yapilir.

    SCRAPFLY (tercih edilen): residential proxy + anti-bot bypass (asp) +
    country=tr (istek basina 25 kredi; datacenter 1 kredi denendi ama Cloudflare
    engelliyor). Yanit JSON zarfi icinde gelir; asil icerik result.content'te.
    Detay/kademe icin bkz. _scrapfly_fetch.

    SCRAPEDO (yedek): geoCode=tr&super=true (residential, istek basina 10 kredi;
    ucretsiz planda datacenter+geoCode Pro plan gerektirdigi icin residential
    zorunlu). (ScrapingAnt denendi ama TR proxy'si olmadigi icin elendi.)"""
    if not _using_proxy():
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Referer": "https://www.trendyol.com/",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    target = urllib.parse.quote(url, safe="")
    # Once UCUZ Scrape.do (residential 10 kredi) denenir; kredisi bittiyse (401)
    # veya hata verirse PAHALI Scrapfly'a (residential 25 kredi) dusulur. Boylece
    # iki bedava motorun aylik kotasi da kullanilir (birlikte ~2 kat toplama).
    if SCRAPEDO_TOKEN:
        try:
            proxied = (f"https://api.scrape.do/?token={SCRAPEDO_TOKEN}"
                       f"&url={target}&geoCode=tr&super=true")
            req = urllib.request.Request(proxied, headers={
                "Accept": "application/json, text/plain, */*",
            })
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read().decode("utf-8", "replace")
            if body:
                return body
        except Exception:
            pass  # Scrape.do kredisi bitmis / hata -> Scrapfly'a dus
    if SCRAPFLY_TOKEN:
        return _scrapfly_fetch(target)
    return ""


def parse_tr_number(val):
    """ '1,7B'->1700, '385,2B'->385200, '2B+'->2000, '500+'->500, '371'->371 """
    if val is None:
        return None, False
    s = str(val).strip()
    plus = "+" in s
    s = s.replace("+", "").strip()
    mult = 1
    up = s.upper()
    if up.endswith("MN"):
        mult = 1000000; s = s[:-2]
    elif up.endswith("M"):
        mult = 1000000; s = s[:-1]
    elif up.endswith("B"):
        mult = 1000; s = s[:-1]
    elif up.endswith("K"):
        mult = 1000; s = s[:-1]
    s = s.replace(".", "").replace(",", ".").strip()
    try:
        return int(round(float(s) * mult)), plus
    except ValueError:
        return None, plus



def _extract_props_json(html):
    """Marka sayfasi HTML'inden gomulu urun JSON'unu cikarir (yedek yontem)."""
    tag = 'window["__single-search-result__PROPS"]='
    i = html.find(tag)
    if i < 0:
        return None
    j = html.find("{", i)
    depth = 0; in_str = False; esc = False
    k = j
    while k < len(html):
        c = html[k]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return html[j:k+1]
        k += 1
    return None


def _find_products(o, d=0):
    if not o or d > 8:
        return None
    if isinstance(o, list) and o and isinstance(o[0], dict) and o[0].get("id") and o[0].get("price"):
        return o
    if isinstance(o, dict):
        for v in o.values():
            r = _find_products(v, d + 1)
            if r:
                return r
    return None


# Ilk calisan sayfadaki ilk 2 urunun HAM JSON'u (rozet/alan adlarini gercek
# veriden gorebilmek icin debug ornegi olarak diske yazilir, siteye yuklenir).
_RAW_PRODUCT_SAMPLE = []
_PRODUCT_URLS = {}  # pid -> detay sayfasi yolu (sepet-kesif icin)


def _merge_page(products, page):
    """Bir listeleme sayfasindaki urunleri products sozlugune ekler (id'ye gore
    tekillestirir). Eklenen YENI urun sayisini doner."""
    added = 0
    for p in page:
        if len(_RAW_PRODUCT_SAMPLE) < 2:
            _RAW_PRODUCT_SAMPLE.append(p)
        pid = p.get("id")
        if pid is None:
            continue
        if p.get("url"):
            _PRODUCT_URLS[pid] = p.get("url")
        if pid in products:
            continue
        pr = p.get("price") or {}
        price = None
        if isinstance(pr, dict):
            price = (pr.get("sellingPrice") or pr.get("discountedPrice") or pr.get("originalPrice"))
        rating = p.get("ratingScore") or {}
        avg = rating.get("averageRating") if isinstance(rating, dict) else None
        imgs = p.get("images") or []
        img = imgs[0] if imgs else None
        if img and not img.startswith("http"):
            img = "https://cdn.dsmcdn.com/mnresize/400/-/" + img.lstrip("/")
        # Sosyal kanit (favori/siparis) zaten katalog sayfasinda gomulu geliyor
        # (socialProof); ayri apigw sosyal-proof API'sine gerek yok.
        fav = order = order_raw = None
        for sp in (p.get("socialProof") or []):
            key = sp.get("key"); val = sp.get("value")
            if key == "favoriteCount":
                fav, _ = parse_tr_number(val)
            elif key == "orderCount":
                order, _ = parse_tr_number(val); order_raw = val
        products[pid] = {
            "id": pid, "name": (p.get("name") or "").strip(), "price": price,
            "ratingCount": rating.get("totalCount") if isinstance(rating, dict) else None,
            "rating": round(avg, 2) if avg else None, "merchantId": p.get("merchantId"),
            "image": img, "favorite": fav, "order": order, "order_raw": order_raw,
            "badges": _extract_badges(p),
        }
        added += 1
    return added


def fetch_catalog_html(products):
    """Marka sayfasini "cok satanlar" (BEST_SELLER) sirasiyla gezerek urunleri
    toplar. Sadece ilk TOP_SELLER_PAGES sayfa alinir; boylece en cok satan urunler
    toplanir, satmayan olu stok hic cekilmez ve istek/kredi maliyeti dusuk kalir.
    Ek olarak "en yeniler" siralamasiyla 1 sayfa daha cekilir ki cok-satan
    listesine girmeyen yepyeni urunler de yakalansin (panel "Yeni Urunler" sekmesi).
    Proxy bazen gecici hata (404/502) veya bos icerik dondurebiliyor; bu yuzden bir
    sayfa "basarisiz" gorununce hemen pes etmek yerine birkac kez daha denenir."""
    pi = 1
    retries = 0
    MAX_RETRIES = 3
    while pi <= TOP_SELLER_PAGES:
        url = f"https://www.trendyol.com/los-ojos-x-b147875?sst=BEST_SELLER&pi={pi}"
        try:
            html = _get(url, validate=lambda b: "__single-search-result__PROPS" in b)
            ps = _extract_props_json(html)
            if not ps:
                raise ValueError("PROPS JSON bulunamadi")
            props = json.loads(ps)
            page = _find_products(props) or []
            if not page:
                raise ValueError("urun listesi bos")
        except Exception as e:
            if retries < MAX_RETRIES:
                retries += 1
                print(f"  ! (html) sayfa {pi} alinamadi ({e}), tekrar deneniyor ({retries}/{MAX_RETRIES})...")
                time.sleep(REQUEST_PAUSE)
                continue
            print(f"  ! (html) sayfa {pi} alinamadi: {e}")
            break
        added = _merge_page(products, page)
        print(f"  (html) sayfa {pi}: toplam {len(products)} urun")
        if added == 0:
            if retries < MAX_RETRIES:
                retries += 1
                print(f"  ! sayfa {pi} yeni urun getirmedi, tekrar deneniyor ({retries}/{MAX_RETRIES})...")
                time.sleep(REQUEST_PAUSE)
                continue
            break
        retries = 0
        pi += 1
        time.sleep(REQUEST_PAUSE)

    # YENI URUNLER SAYFASI (best-effort, +1 istek): cok-satan ilk sayfalarina
    # girmeyen yepyeni urunleri yakalamak icin "en yeniler" (MOST_RECENT)
    # siralamasiyla 1 sayfa daha cekilir; satis/favori verileri de ayni sayfadan
    # gelir. Basarisiz olursa toplama BOZULMAZ, sadece not dusulur.
    try:
        url = f"https://www.trendyol.com/los-ojos-x-b147875?sst=MOST_RECENT&pi=1"
        html = _get(url, validate=lambda b: "__single-search-result__PROPS" in b)
        ps = _extract_props_json(html)
        page = (_find_products(json.loads(ps)) or []) if ps else []
        added = _merge_page(products, page)
        print(f"  (html) en-yeniler sayfasi: +{added} yeni urun (toplam {len(products)})")
    except Exception as e:
        print(f"  ! en-yeniler sayfasi alinamadi (toplama devam ediyor): {e}")
    return products


# Sosyal-kanit API'sinin bilinen alan adlari -> bizim kisa adlarimiz.
# Bilinmeyen yeni alanlar da atlanmaz, "extra" icinde aynen saklanir.
SOCIAL_KEYS = {
    "orderCountL3D": "order3d",   # son 3 gunde satis adedi
    "basketCount": "basket",      # su an kac kisinin sepetinde
    "favoriteCount": "favorite",  # favori sayisi
    "pageViewCount": "views",     # goruntulenme
    "questionCount": "questions", # urune sorulan soru sayisi (varsa)
}


def fetch_social(product_ids):
    """Her urunun sosyal-kanit verilerini ceker: son 3 gunde satis, sepette
    kac kisi var, goruntulenme, favori vb. (sepete ekleme ekraninda gorunen
    sayilar). 24'lu gruplar halinde topluca sorulur; 240 urun icin sadece 10
    istek. Bu servis proxy uzerinden 502/422 veriyordu, o yuzden YALNIZCA
    dogrudan Turkiye baglantisinda (proxy'siz) cagrilir — Turkiye-PC
    kurulumunda istekler bedava oldugu icin maliyeti yok. Basarisiz gruplar
    atlanir, toplama bozulmaz. API'nin dondurdugu TUM alanlar saklanir;
    taniniyorsa kisa adla, tanimiyorsak 'extra' sozlugunde ham haliyle."""
    out = {}
    raw_sample = {}
    batch = 24
    for i in range(0, len(product_ids), batch):
        chunk = product_ids[i:i + batch]
        ids = ",".join(str(x) for x in chunk)
        url = (f"{SOCIAL_API}?contentIds={ids}&channelId=1&storefrontId=1"
               f"&culture=tr-TR&countryCode=TR")
        try:
            data = json.loads(_get(url))
        except Exception as e:
            print(f"  ! sosyal kanit grubu alinamadi: {e}")
            time.sleep(REQUEST_PAUSE)
            continue
        for pid, arr in (data.get("data") or {}).items():
            rec = {}
            for o in arr or []:
                k = o.get("key"); v = o.get("value")
                if not k:
                    continue
                num, _ = parse_tr_number(v)
                if k in SOCIAL_KEYS:
                    rec[SOCIAL_KEYS[k]] = num
                    rec[SOCIAL_KEYS[k] + "_raw"] = v
                else:
                    rec.setdefault("extra", {})[k] = v
            if rec:
                out[str(pid)] = rec
            if not raw_sample:
                raw_sample[str(pid)] = arr
        time.sleep(REQUEST_PAUSE)
    out["_raw_sample"] = raw_sample
    return out


# Rozet tipi kodlarinin Turkce karsiligi (sira numarali rozetlerde kullanilir)
_BADGE_TYPE_TR = {"BEST_SELLER": "Çok Satan", "MOST_FAVOURITE": "En Çok Favorilenen"}


def _extract_badges(p):
    """Urun kartindaki rozet benzeri her seyi metin listesi olarak toplar
    ('Cok Satan #1', 'Hizli Satici', 'Kargo Bedava', kampanya etiketi vb.).
    Gercek veride (debug_ornek.json, 2026-07-02) rozetler ic ice sozluklerde:
    badges={topRankingBadge:{title:'1',type:'BEST_SELLER'}, unknownBadge:
    {title:'Hizli Satici'}}, stripBadge={type:'BEST_SELLER',title:'1'},
    simplifiedBadges=[{title:'Kargo Bedava'},...]. Bu yuzden sozluklerin
    icine de girilir; 'title' sadece sira numarasiysa tip adiyla birlestirilir
    ('Çok Satan #1'). Ilk siraya en degerli rozet (sira rozeti) gelir."""
    texts = []
    def _add(t):
        t = (t or "").strip()
        if t and len(t) <= 60 and not t.startswith("http") and t not in texts:
            texts.append(t)
    def _pull(v, depth=0):
        if v is None or depth > 3:
            return
        if isinstance(v, str):
            _add(v)
        elif isinstance(v, dict):
            title = None
            for kk in ("title", "text", "name", "label", "description"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    title = v[kk].strip(); break
            if title:
                typ = v.get("type")
                if title.replace(".", "").isdigit() and typ:
                    _add(f"{_BADGE_TYPE_TR.get(typ, typ)} #{title.rstrip('.')}")
                else:
                    _add(title)
            else:
                for vv in v.values():
                    _pull(vv, depth + 1)
        elif isinstance(v, list):
            for it in v:
                _pull(it, depth + 1)
    # Sira onemli: panel ilk rozeti one cikarir -> en degerli olan basta.
    for field in ("stripBadge", "badges", "topRankings", "dealBadge", "stamps",
                  "labels", "socialProofHighlight", "variantBadge",
                  "simplifiedBadges"):
        _pull(p.get(field))
    return texts or None


def fetch_catalog():
    if _using_proxy() or os.environ.get("FORCE_HTML") == "1":
        # apigw.trendyol.com proxy uzerinden cogunlukla 502 (ROTATION_FAILED) donuyor,
        # bosuna kota harcamamak icin dogrudan HTML yontemine gidiyoruz.
        # FORCE_HTML=1: Turkiye-PC kurulumu da HTML yolunu kullanir, cunku
        # favori/siparis (socialProof) verisi yalnizca HTML sayfasinda geliyor.
        return fetch_catalog_html({})

    products = {}
    for pi in range(1, MAX_PAGES + 1):
        url = (f"{SEARCH_API}?wb={BRAND_ID}&pi={pi}&culture=tr-TR"
               f"&storefrontId=1&countryCode=TR")
        try:
            data = json.loads(_get(url))
        except Exception as e:
            print(f"  ! sayfa {pi} alinamadi: {e}")
            break
        result = data.get("result", data)
        page_products = (result.get("products")
                         or (result.get("searchResult") or {}).get("products") or [])
        if not page_products:
            break
        for p in page_products:
            pid = p.get("id")
            if pid is None or pid in products:
                continue
            pr = p.get("price") or {}
            price = None
            if isinstance(pr, dict):
                price = (pr.get("sellingPrice") or pr.get("discountedPrice")
                         or pr.get("originalPrice"))
            rating = p.get("ratingScore") or {}
            avg = rating.get("averageRating") if isinstance(rating, dict) else None
            imgs = p.get("images") or []
            img = imgs[0] if imgs else None
            if img and not img.startswith("http"):
                img = "https://cdn.dsmcdn.com/mnresize/400/-/" + img.lstrip("/")
            products[pid] = {
                "id": pid,
                "name": (p.get("name") or "").strip(),
                "price": price,
                "ratingCount": rating.get("totalCount") if isinstance(rating, dict) else None,
                "rating": round(avg, 2) if avg else None,
                "merchantId": p.get("merchantId"),
                "image": img,
            }
        print(f"  sayfa {pi}: toplam {len(products)} urun")
        time.sleep(REQUEST_PAUSE)
    if not products:
        print("  API urun vermedi, HTML yedek yontemi deneniyor...")
        products = fetch_catalog_html(products)
    return products


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def main():
    today = datetime.date.today().isoformat()
    print(f"Los Ojos veri toplama basladi - {today}")

    print("1) Urun katalogu cekiliyor...")
    catalog = fetch_catalog()
    if not catalog:
        print("HATA: Hic urun alinamadi. (Trendyol IP'yi engellemis olabilir.)")
        sys.exit(1)
    ids = list(catalog.keys())
    print(f"   {len(ids)} urun bulundu (favori/siparis dahil, katalogdan).")

    # Sosyal-kanit verileri (3 gunde satis, sepette, goruntulenme, soru vb.):
    # yalnizca dogrudan (proxy'siz, Turkiye icinden) baglantida cekilir;
    # proxy uzerinden bu servis calismiyor + kredi yakar.
    social = {}
    social_raw_sample = {}
    if not _using_proxy():
        print("2) Sosyal kanit verileri cekiliyor (3g satis / sepet / goruntulenme)...")
        social = fetch_social(ids)
        social_raw_sample = social.pop("_raw_sample", {})
        print(f"   {len(social)} urun icin sosyal kanit verisi geldi.")

        # Soru sayisi KESFI: soru adedi katalog/sosyal serviste yok, urun detay
        # sayfasinda geciyor. Alan adini gercek veriden gorebilmek icin 1 urunun
        # detay HTML'inden 'question' geçen parcalar debug dosyasina kaydedilir;
        # alan netlesince gercek toplama eklenecek. (+1 istek, best-effort)
        soru_parcalari = []
        try:
            import re
            u = _RAW_PRODUCT_SAMPLE[0].get("url") if _RAW_PRODUCT_SAMPLE else None
            if u:
                dhtml = _get("https://www.trendyol.com" + u)
                soru_parcalari = re.findall(
                    r".{60}[Qq]uestion.{160}", dhtml)[:15]
                print(f"   soru-kesfi: detay sayfasinda {len(soru_parcalari)} "
                      f"'question' parcasi bulundu.")
        except Exception as e:
            print(f"  ! soru-kesfi atlandi: {e}")

        # SEPET-KESIF: kullanicinin sepet ekraninda gordugu 'X tanesi satildi' /
        # 'Y kisinin sepetinde' sayilari (kucuk sayilar dahil) katalog ve sosyal
        # serviste yok. Kaynagi bulmak icin: siparis verisi OLMAYAN 3 urunun
        # (a) detay sayfasi HTML'indeki ilgili anahtar kelime parcalari,
        # (b) birkac aday API adresinin ham cevabi debug dosyasina yazilir.
        # Alan netlesince gercek toplama eklenecek. (best-effort, ~12 istek)
        sepet_kesif = {}
        try:
            import re
            adaylar = [pid for pid in ids
                       if not catalog[pid].get("order")
                       and not social.get(str(pid), {}).get("order")][:3]
            for pid in adaylar:
                rec = {}
                u = _PRODUCT_URLS.get(pid)
                if u:
                    try:
                        dhtml = _get("https://www.trendyol.com" + u)
                        frags = []
                        for kw in ("socialProof", "orderCount", "basketCount",
                                   "soldCount", "tanesi", "sat\\u0131ld"):
                            frags += re.findall(".{80}" + kw + ".{200}", dhtml)[:3]
                        rec["detay_parcalari"] = frags[:12]
                    except Exception as e:
                        rec["detay_parcalari"] = f"HATA: {e}"
                for adi, au in {
                    "sosyal_screen_basket":
                        f"{SOCIAL_API}?contentIds={pid}&channelId=1&storefrontId=1"
                        f"&culture=tr-TR&countryCode=TR&screen=basket",
                    "productgw_socialproof":
                        f"https://apigw.trendyol.com/discovery-web-productgw-service"
                        f"/api/social-proof/{pid}?channelId=1",
                    "socialproof_service":
                        f"https://apigw.trendyol.com/discovery-sfint-social-proof-service"
                        f"/api/social-proof/?contentIds={pid}&channelId=1&storefrontId=1"
                        f"&culture=tr-TR&countryCode=TR",
                }.items():
                    try:
                        rec[adi] = _get(au)[:800]
                    except Exception as e:
                        rec[adi] = f"HATA: {e}"
                    time.sleep(REQUEST_PAUSE)
                sepet_kesif[str(pid)] = rec
            print(f"   sepet-kesfi: {len(adaylar)} urun icin aday kaynaklar denendi.")
        except Exception as e:
            sepet_kesif["hata"] = str(e)

        # Debug ornegi: rozet/alan adlarini gercek veriden gorebilmek icin ilk
        # urunlerin ham JSON'u diske yazilir; Turkiye-PC bunu siteye de yukler.
        save_json(os.path.join(DATA_DIR, "debug_ornek.json"), {
            "date": today,
            "katalog_ham_urunler": _RAW_PRODUCT_SAMPLE,
            "sosyal_api_ham": social_raw_sample,
            "detay_soru_parcalari": soru_parcalari,
            "sepet_kesif": sepet_kesif,
        })

    # Mevcut latest.json'dan firstSeen tarihlerini al
    old_latest = load_json(os.path.join(DATA_DIR, "latest.json"), {})
    old_products = old_latest.get("products", {})

    # firstSeen guvencesi: bir urun bir gun listeden dusup ertesi gun geri
    # gelirse latest.json'daki kaydi kaybolur ve yanlislikla "bugun ilk kez
    # gorunmus" sayilirdi (sahte YENI rozeti). Bu yuzden gecmis arsivden (history)
    # her urunun GERCEK ilk gorulme tarihi cikarilir ve yedek olarak kullanilir.
    history = load_json(os.path.join(DATA_DIR, "history.json"),
                        {"brand": "Los Ojos", "snapshots": []})
    hist_first = {}
    for s in sorted(history.get("snapshots", []), key=lambda x: x.get("date", "")):
        for hp in s.get("products", {}):
            hist_first.setdefault(hp, s.get("date"))

    snapshot = {}
    for pid, info in catalog.items():
        pid_str = str(pid)
        # firstSeen: eski kayitta varsa koru, yoksa arsivdeki en eski tarih, o da yoksa bugun
        first_seen = (old_products.get(pid_str, {}).get("firstSeen")
                      or hist_first.get(pid_str) or today)
        sp = social.get(pid_str, {})
        old = old_products.get(pid_str, {})
        # ONEMLI: order3d/basket/views icin asil kaynak Murat-PC'nin sepet-servisi
        # toplayicisi (satis_topla.py); bu toplayicinin sosyal verisi Trendyol
        # esikleri yuzunden cogunlukla BOS gelir. Bos alanlarda onceki latest.json
        # degeri korunur; yoksa gunun ikinci calismasi ilkinin satis verisini
        # silip paneli 39 urunden 2 urune dusuruyordu (2026-07-07 yasandi).
        snapshot[pid_str] = {
            "name": info["name"], "price": info["price"],
            "ratingCount": info["ratingCount"], "rating": info["rating"],
            "order": info.get("order"), "order_raw": info.get("order_raw"),
            # katalogdaki favori bossa sosyal API'den tamamla
            "favorite": info.get("favorite") if info.get("favorite") is not None else sp.get("favorite"),
            "order3d": sp.get("order3d") if sp.get("order3d") is not None else old.get("order3d"),
            "order3d_raw": sp.get("order3d_raw") if sp.get("order3d") is not None else old.get("order3d_raw"),
            "basket": sp.get("basket") if sp.get("basket") is not None else old.get("basket"),
            "views": sp.get("views") if sp.get("views") is not None else old.get("views"),
            "questions": sp.get("questions") if sp.get("questions") is not None else old.get("questions"),
            "social_extra": sp.get("extra"),
            "badges": info.get("badges"),
            "image": info.get("image"),
            "firstSeen": first_seen,
        }

    save_json(os.path.join(DATA_DIR, "latest.json"),
              {"collectedAt": today, "brand": "Los Ojos",
               "productCount": len(catalog), "products": snapshot})

    history["snapshots"] = [s for s in history["snapshots"] if s.get("date") != today]
    compact = {"date": today, "products": {}}
    for pid, pr in snapshot.items():
        rec = {
            "rc": pr["ratingCount"], "o": pr["order"],
            "f": pr["favorite"], "p": pr["price"],
        }
        # Yeni sosyal alanlar yalnizca doluysa yazilir (arsiv sisirmesin)
        for src, dst in (("order3d", "o3d"), ("basket", "b"),
                         ("views", "v"), ("questions", "q")):
            if pr.get(src) is not None:
                rec[dst] = pr[src]
        compact["products"][pid] = rec
    history["snapshots"].append(compact)
    history["snapshots"].sort(key=lambda s: s["date"])
    history["names"] = {pid: pr["name"] for pid, pr in snapshot.items()}
    save_json(os.path.join(DATA_DIR, "history.json"), history)

    n_order = sum(1 for p in snapshot.values() if p["order"])
    n_o3d = sum(1 for p in snapshot.values() if p.get("order3d") is not None)
    n_badge = sum(1 for p in snapshot.values() if p.get("badges"))
    print(f"TAMAM. {len(catalog)} urun kaydedildi, {n_order} urunde siparis rozeti, "
          f"{n_o3d} urunde 3-gunluk satis, {n_badge} urunde rozet var.")
    print(f"Arsivde {len(history['snapshots'])} gunluk veri birikti.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Lexwatch — Script de collecte
Archivage illimité : data.json grandit indéfiniment.
Collecte RSS + Judilibre + Légifrance → génère data.json
 
Lancement : python collect.py
"""
 
import json, os, re, hashlib, feedparser, requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
 
# ── CONFIG ──────────────────────────────────
JUDILIBRE_API_KEY      = os.getenv("JUDILIBRE_API_KEY", "")
LEGIFRANCE_CLIENT_ID   = os.getenv("LEGIFRANCE_CLIENT_ID", "")
LEGIFRANCE_CLIENT_SECRET = os.getenv("LEGIFRANCE_CLIENT_SECRET", "")
ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
 
# Pas de limite de jours — archivage infini
OUTPUT_FILE = Path("data.json")
 
# ── MOTS-CLÉS ───────────────────────────────
KEYWORDS = {
    "justice":      ["arrêt","décision","jugement","condamnation","cour de cassation",
                     "cour d'appel","tribunal","contrefaçon","droits voisins","droit d'auteur",
                     "plagiat","phonogramme","redevance","streaming","sample","licence"],
    "institutionnel":["nomination","nommé","président","directeur","conseil d'administration",
                     "ARCOM","CSA","SACEM","SCPP","SPPF","ADAMI","SPEDIDAM",
                     "ministère de la culture","mission","rapport"],
    "musique":      ["musique","music","streaming","label","artiste","album","SACEM","IFPI",
                     "Billboard","Spotify","Deezer","Apple Music","YouTube Music","phonogramme",
                     "producteur","auteur-compositeur","droit voisin","Universal","Sony Music",
                     "Warner Music","concert","live"],
    "ia":           ["intelligence artificielle","IA","AI","algorithme","machine learning",
                     "deepfake","génératif","generative","ChatGPT","modèle de langage",
                     "données d'entraînement","EU AI Act","régulation numérique",
                     "plateforme","recommandation","numérique"],
    "loi":          ["loi","décret","ordonnance","directive","règlement","journal officiel",
                     "transposition","DADVSI","HADOPI","projet de loi","code de la propriété"],
    "cnil":         ["CNIL","RGPD","données personnelles","data protection","DPA",
                     "sanction","mise en demeure","délibération","cookies",
                     "traitement de données","vie privée","privacy","biométrique","consentement"],
}
 
# ── SOURCES RSS ─────────────────────────────
# lang:"fr" = source française (prioritaire)
# lang:"en" = source anglaise (résumé traduit en français par l'IA)
RSS_SOURCES = [
    # ── FR · Régulation & institutionnel ──
    {"url":"https://www.cnil.fr/fr/rss.xml",
     "source":"CNIL", "default_cat":"cnil", "lang":"fr"},
    {"url":"https://www.arcom.fr/nos-ressources/espace-presse/communiques-de-presse/rss",
     "source":"ARCOM", "default_cat":"institutionnel", "lang":"fr"},
    {"url":"https://www.culture.gouv.fr/Presse/Communiques-de-presse/rss",
     "source":"Ministère de la Culture", "default_cat":"institutionnel", "lang":"fr"},
 
    # ── FR · Industrie musicale ──
    {"url":"https://www.sacem.fr/rss/actualites",
     "source":"SACEM", "default_cat":"musique", "lang":"fr"},
    {"url":"https://www.snepmusique.com/feed/",
     "source":"SNEP", "default_cat":"musique", "lang":"fr"},
    {"url":"https://www.irma.asso.fr/spip.php?page=backend",
     "source":"IRMA", "default_cat":"musique", "lang":"fr"},
 
    # ── FR · Droit & juridique ──
    {"url":"https://www.dalloz-actualite.fr/rss/tous-les-contenus",
     "source":"Dalloz Actualité", "default_cat":"justice", "lang":"fr"},
    {"url":"https://www.legalis.net/feed/",
     "source":"Légalis", "default_cat":"ia", "lang":"fr"},
    {"url":"https://www.village-justice.com/rss/rss.xml",
     "source":"Village de la Justice", "default_cat":"justice", "lang":"fr"},
    {"url":"https://www.net-iris.fr/veille-juridique/rss.php",
     "source":"Net-Iris", "default_cat":"justice", "lang":"fr"},
 
    # ── FR · Numérique & IA ──
    {"url":"https://www.numerama.com/feed/",
     "source":"Numerama", "default_cat":"ia", "lang":"fr"},
    {"url":"https://next.ink/feed/",
     "source":"Next INpact", "default_cat":"ia", "lang":"fr"},
 
    # ── FR · Journal Officiel ──
    {"url":"https://www.legifrance.gouv.fr/feeds/jorf",
     "source":"Journal Officiel", "default_cat":"loi", "lang":"fr"},
 
    # ── EN · Industrie musicale internationale (résumés traduits) ──
    {"url":"https://www.billboard.com/feed/",
     "source":"Billboard", "default_cat":"musique", "lang":"en"},
    {"url":"https://musically.com/feed/",
     "source":"Music Ally", "default_cat":"musique", "lang":"en"},
    {"url":"https://www.musicbusinessworldwide.com/feed/",
     "source":"Music Business Worldwide", "default_cat":"musique", "lang":"en"},
    {"url":"https://www.ifpi.org/feed/",
     "source":"IFPI", "default_cat":"musique", "lang":"en"},
]
 
# ── UTILS ────────────────────────────────────
def make_id(title, source):
    return hashlib.md5(f"{source}:{title}".encode()).hexdigest()[:12]
 
def detect_category(text):
    text_lower = text.lower()
    scores = {cat: sum(1 for kw in kws if kw.lower() in text_lower) for cat, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "musique"
 
def is_relevant(text):
    all_kws = [kw for kws in KEYWORDS.values() for kw in kws]
    return any(kw.lower() in text.lower() for kw in all_kws)
 
def parse_date(entry):
    import calendar
    for field in ["published_parsed", "updated_parsed"]:
        val = getattr(entry, field, None)
        if val:
            return datetime.fromtimestamp(calendar.timegm(val), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)
 
def clean_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()
 
def truncate(text, n=300):
    text = text.strip()
    return text if len(text) <= n else text[:n].rsplit(' ', 1)[0] + '…'
 
# ── RÉSUMÉ IA ───────────────────────────────
def generate_summary(title, description, lang="fr"):
    """
    Résumé IA toujours rédigé en français.
    - Sources FR : résumé direct
    - Sources EN : traduction + résumé en français
    Si pas de clé Anthropic : extrait brut (tronqué, sans traduction).
    """
    if not ANTHROPIC_API_KEY:
        return truncate(clean_html(description), 280), "extrait source"
 
    if lang == "en":
        instruction = (
            "Tu es juriste spécialisé en droit de la musique et du numérique. "
            "Traduis et résume en français en 2 phrases maximum (200 caractères max) "
            "cet article anglais pour une veille juridique française. "
            f"Titre : {title}\nContenu : {clean_html(description)[:500]}\n"
            "Réponds uniquement avec le résumé en français, sans introduction."
        )
        stype = "résumé IA (traduit)"
    else:
        instruction = (
            "Tu es juriste spécialisé en droit de la musique et du numérique. "
            "Résume en français en 2 phrases maximum (200 caractères max) "
            "cet article pour une veille juridique. "
            f"Titre : {title}\nContenu : {clean_html(description)[:500]}\n"
            "Réponds uniquement avec le résumé en français, sans introduction."
        )
        stype = "résumé IA"
 
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":150,"messages":[{
                "role":"user", "content": instruction
            }]},
            timeout=10
        )
        if resp.status_code == 200:
            text = resp.json().get("content",[{}])[0].get("text","").strip()
            if text: return text, stype
    except Exception as e:
        print(f"  ⚠️  Résumé IA : {e}")
    return truncate(clean_html(description), 280), "extrait source"
 
# ── COLLECTE RSS ─────────────────────────────
def collect_rss():
    articles = []
    # On ne prend que ce qui est publié depuis hier (évite les doublons déjà archivés)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=2)
    for cfg in RSS_SOURCES:
        print(f"  📡 {cfg['source']}…")
        try:
            feed = feedparser.parse(cfg['url'])
        except Exception as e:
            print(f"    ⚠️  {e}"); continue
        for entry in feed.entries[:25]:
            title = getattr(entry,'title','').strip()
            link  = getattr(entry,'link','')
            desc  = getattr(entry,'summary','') or getattr(entry,'description','')
            pub   = parse_date(entry)
            if pub < cutoff: continue
            full  = f"{title} {clean_html(desc)}"
            if not is_relevant(full): continue
            cat   = detect_category(full) or cfg['default_cat']
            summary, stype = generate_summary(title, desc, lang=cfg.get('lang','fr'))
            articles.append({"id":make_id(title,cfg['source']),"title":title,"source":cfg['source'],
                             "url":link,"category":cat,"published_at":pub.isoformat(),
                             "summary":summary,"summary_type":stype})
    return articles
 
# ── JUDILIBRE ────────────────────────────────
def collect_judilibre():
    if not JUDILIBRE_API_KEY:
        print("  ⚠️  JUDILIBRE_API_KEY manquante — skip"); return []
    articles = []
    print("  ⚖️  Judilibre…")
    terms = ["droit d'auteur","droits voisins","phonogramme","contrefaçon musicale","streaming"]
    for term in terms[:3]:
        try:
            r = requests.get(
                "https://api.piste.gouv.fr/cassation/judilibre/v1.0/search",
                params={"query":term,"date_start":(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"),
                        "date_end":datetime.now().strftime("%Y-%m-%d"),"page_size":5,"resolve_references":"true"},
                headers={"KeyId":JUDILIBRE_API_KEY}, timeout=15)
            if r.status_code != 200: continue
            for item in r.json().get("results",[]):
                title = item.get("summary", item.get("numero","Décision"))[:150]
                link  = f"https://www.courdecassation.fr/decision/{item.get('id','')}"
                try: pub = datetime.fromisoformat(item.get("decision_date","")).replace(tzinfo=timezone.utc)
                except: pub = datetime.now(tz=timezone.utc)
                summary, stype = generate_summary(title, item.get("text","Décision Cour de Cassation")[:500])
                articles.append({"id":make_id(title,"CdC"),"title":f"Cour de Cassation — {title}",
                                 "source":"Judilibre / Cour de Cassation","url":link,"category":"justice",
                                 "published_at":pub.isoformat(),"summary":summary,"summary_type":stype})
        except Exception as e:
            print(f"    ⚠️  Judilibre ({term}): {e}")
    seen = set(); unique = []
    for a in articles:
        if a["id"] not in seen: seen.add(a["id"]); unique.append(a)
    return unique
 
# ── LÉGIFRANCE ───────────────────────────────
def get_legifrance_token():
    if not LEGIFRANCE_CLIENT_ID: return None
    try:
        r = requests.post("https://oauth.piste.gouv.fr/api/oauth/token",
            data={"grant_type":"client_credentials","client_id":LEGIFRANCE_CLIENT_ID,
                  "client_secret":LEGIFRANCE_CLIENT_SECRET,"scope":"openid"}, timeout=10)
        if r.status_code == 200: return r.json().get("access_token")
    except Exception as e: print(f"  ⚠️  Token Légifrance : {e}")
    return None
 
def collect_legifrance():
    token = get_legifrance_token()
    if not token: print("  ⚠️  Légifrance non configuré — skip"); return []
    articles = []
    print("  📜 Légifrance…")
    for kw in ["musique","droit d'auteur"][:2]:
        try:
            r = requests.post(
                "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app/consult/jorf/search",
                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                json={"recherche":{"champs":[{"typeChamp":"TITLE","criteres":[{"typeRecherche":"TOUS_LES_MOTS_DANS_UN_CHAMP","valeur":kw}]}],
                                   "filtres":[{"facette":"DATE_VERSION","valeur":(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")}],
                                   "pageNumber":1,"pageSize":3}}, timeout=15)
            if r.status_code != 200: continue
            for item in r.json().get("results",[]):
                title = item.get("title","")
                if not title: continue
                try: pub = datetime.strptime(item.get("dateParution",""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except: pub = datetime.now(tz=timezone.utc)
                summary, stype = generate_summary(title, item.get("texteHtml",""))
                articles.append({"id":make_id(title,"Légifrance"),"title":title,
                                 "source":"Légifrance / Journal Officiel",
                                 "url":f"https://www.legifrance.gouv.fr/jorf/id/{item.get('id','')}",
                                 "category":"loi","published_at":pub.isoformat(),"summary":summary,"summary_type":stype})
        except Exception as e: print(f"    ⚠️  Légifrance ({kw}): {e}")
    return articles
 
# ── FUSION ARCHIVAGE ILLIMITÉ ─────────────────
def load_existing():
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, encoding='utf-8') as f: return json.load(f)
        except: pass
    return {"generated_at":"","days":[]}
 
def merge(existing, new_articles):
    """
    Fusionne sans limite de jours.
    L'archive grandit indéfiniment — aucune purge.
    """
    # Index global pour dédoublonner
    existing_ids = {a["id"] for day in existing.get("days",[]) for a in day.get("articles",[])}
 
    # Construire dict {date: [articles]}
    days_dict = {day["date"]: day["articles"] for day in existing.get("days",[])}
 
    today_str = datetime.now().strftime("%Y-%m-%d")
    if today_str not in days_dict:
        days_dict[today_str] = []
 
    added = 0
    for art in new_articles:
        if art["id"] in existing_ids: continue
        art_date = art["published_at"][:10]
        if art_date not in days_dict: days_dict[art_date] = []
        days_dict[art_date].append(art)
        existing_ids.add(art["id"])
        added += 1
 
    # Tri décroissant, aucune limite
    sorted_days = sorted(
        [{"date":d, "articles":sorted(arts, key=lambda a: a["published_at"], reverse=True)}
         for d, arts in days_dict.items()],
        key=lambda x: x["date"], reverse=True
    )
 
    print(f"  ➕ {added} nouveaux articles ajoutés à l'archive")
    return {"generated_at": datetime.now(tz=timezone.utc).isoformat(), "days": sorted_days}
 
# ── MAIN ─────────────────────────────────────
def main():
    print("\n🔍 Lexwatch — Collecte en cours…\n")
    all_articles = []
 
    print("📡 RSS…")
    all_articles += collect_rss()
    print(f"   → {len(all_articles)} articles RSS\n")
 
    print("⚖️  Judilibre…")
    j = collect_judilibre()
    all_articles += j
    print(f"   → {len(j)} décisions\n")
 
    print("📜 Légifrance…")
    lf = collect_legifrance()
    all_articles += lf
    print(f"   → {len(lf)} textes\n")
 
    existing = load_existing()
    merged   = merge(existing, all_articles)
 
    total = sum(len(d["articles"]) for d in merged["days"])
    print(f"\n📚 Archive : {total} articles sur {len(merged['days'])} jours (archivage illimité)")
 
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
 
    print(f"✅ data.json mis à jour\n")
 
if __name__ == "__main__":
    main()

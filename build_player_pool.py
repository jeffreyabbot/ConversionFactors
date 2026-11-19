import io
import os
import re
import time
import urllib.parse
import urllib.request
import pandas as pd
from bs4 import BeautifulSoup

# ==========================================
# CONFIGURATION (EDIT THIS LIST ANYTIME)
# ==========================================
TORVIK_YEAR = 2026          # 2026 = full 2025-26 NCAA D1 season
REALGM_SEASON = 2026        # 2026 = 2025-26 season (use 2027 for current 2026-27 season)
PAGES_PER_LEAGUE = 3        # 3 pages = top 300 players per league (100 per page)
USE_LOCAL_BROWSER = True    # True = Free ($0) via SeleniumBase Chrome; False = uses SCRAPERAPI_KEY
SCRAPERAPI_KEY = ""         # Only needed if USE_LOCAL_BROWSER = False
OUTPUT_CSV = "player_pool.csv"

INCLUDE_NCAA_D1 = True
INCLUDE_NCAA_D2 = True
INCLUDE_NCAA_D3 = True

# Map RealGM International League Names (exact text on RealGM) -> Your App's League Names
REALGM_TO_APP_LEAGUE = {
    "Spanish Primera FEB": "Spain Primera FEB",
    "Spanish Segunda FEB": "Spain Segunda FEB",
    "Spanish Liga U": "Spanish Liga U",
    "Spanish ACB": "Spain Liga ACB",
    "French LNB Pro B": "France Pro B",
    "French NM1": "France NM1",
    "German Pro A": "Germany Pro A",
    "German Pro B": "Germany Pro B",
    "BNXT League": "BNXT (Belgian/Top)",
    "Portuguese LPB": "Portugal LPB (Rest)",
    "Super League Basketball": "British Super League (SLB)",
    "Italian Serie A2 Basket": "Italy Lega A2",
    "Lithuanian NKL": "Lithuania NKL",
    "Swedish Basketligan": "Sweden Basketligan",
    "Danish Basketligaen": "Danish Basketligaen",
    "Estonian-Latvian Basketball League": "Estonia-Latvia (ELBL)",
    "Polish 1 Liga": "Poland 1 Liga",
    "Greek HEBA A2": "Greece Elite League (A2)",
}

# ==========================================
# 1. NCAA D1 (BARTTORVIK - FREE BULK CSV)
# ==========================================
TORVIK_CONF_TO_TIER = {
    "ACC": "NCAA D1 (High Major)", "A10": "NCAA D1 (High Major)",
    "B12": "NCAA D1 (High Major)", "BE": "NCAA D1 (High Major)",
    "B10": "NCAA D1 (High Major)", "MWC": "NCAA D1 (High Major)",
    "P12": "NCAA D1 (High Major)", "SEC": "NCAA D1 (High Major)",
    "Amer": "NCAA D1 (Mid Major)", "BSky": "NCAA D1 (Mid Major)",
    "BW": "NCAA D1 (Mid Major)", "CAA": "NCAA D1 (Mid Major)",
    "CUSA": "NCAA D1 (Mid Major)", "Ivy": "NCAA D1 (Mid Major)",
    "MAC": "NCAA D1 (Mid Major)", "MVC": "NCAA D1 (Mid Major)",
    "WCC": "NCAA D1 (Mid Major)", "WAC": "NCAA D1 (Mid Major)",
    "AE": "NCAA D1 (Low Major)", "ASun": "NCAA D1 (Low Major)",
    "BSth": "NCAA D1 (Low Major)", "Horz": "NCAA D1 (Low Major)",
    "MAAC": "NCAA D1 (Low Major)", "MEAC": "NCAA D1 (Low Major)",
    "NEC": "NCAA D1 (Low Major)", "OVC": "NCAA D1 (Low Major)",
    "Pat": "NCAA D1 (Low Major)", "SC": "NCAA D1 (Low Major)",
    "Slnd": "NCAA D1 (Low Major)", "SWAC": "NCAA D1 (Low Major)",
    "Sum": "NCAA D1 (Low Major)", "SB": "NCAA D1 (Low Major)",
}
YR_TO_YOB = {"Fr": 2007, "So": 2006, "Jr": 2005, "Sr": 2004}

def ht_to_cm(ht_str):
    m = re.match(r"(\d)-(\d{1,2})", str(ht_str).strip())
    if not m:
        return 193
    return round((int(m.group(1)) * 12 + int(m.group(2))) * 2.54)

def infer_position(ht_cm, ast, tpa_pg, tp_pct, treb=0.0, blk=0.0):
    if ht_cm >= 201 or (ht_cm == 0 and (treb >= 6.0 or blk >= 0.8)):
        return "Bigs (C/PF)"
    if ast >= 3.0 and (ht_cm <= 193 or ht_cm == 0):
        return "Slashers/PGs"
    if tpa_pg >= 3.0 and tp_pct >= 0.32:
        return "Shooters (SG/SF)"
    return "Slashers/PGs" if ht_cm <= 191 else "Slashers / D(Non-Shooters)"

def build_torvik_pool(year):
    print(f"Fetching BartTorvik NCAA D1 ({year})...")
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    try:
        from curl_cffi import requests as cf_requests
        text = cf_requests.get(url, impersonate="chrome120", timeout=30).text
    except ImportError:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", errors="ignore")

    raw = pd.read_csv(io.StringIO(text), header=None)
    rows = []
    for _, r in raw.iterrows():
        try:
            gp = int(float(r[3]))
            mp = float(r[54])
            if gp < 8 or mp < 10.0:
                continue
            conf = str(r[2]).strip()
            ht_cm = ht_to_cm(r[26])
            ftm_pg, fta_pg = float(r[13]) / gp, float(r[14]) / gp
            twom_pg, twoa_pg = float(r[16]) / gp, float(r[17]) / gp
            tpm_pg, tpa_pg = float(r[19]) / gp, float(r[20]) / gp
            fgm_pg, fga_pg = twom_pg + tpm_pg, twoa_pg + tpa_pg
            tp_pct = tpm_pg / tpa_pg if tpa_pg > 0 else 0.0
            ast = float(r[60])
            ast_tov = float(r[35]) if pd.notna(r[35]) else 0.0
            to_per = float(r[12]) if pd.notna(r[12]) else 15.0
            tov = (ast / ast_tov) if ast_tov > 0 else (to_per / 100.0) * (fga_pg + 0.44 * fta_pg)
            pf = ((float(r[30]) if pd.notna(r[30]) else 3.0) / 40.0) * mp

            rows.append({
                "Source": f"BartTorvik {year}",
                "Player": str(r[0]).strip(),
                "Team": str(r[1]).strip(),
                "Origin League": TORVIK_CONF_TO_TIER.get(conf, "NCAA D1 (Low Major)"),
                "Conference": conf,
                "Position": infer_position(ht_cm, ast, tpa_pg, tp_pct, float(r[59]), float(r[62])),
                "YOB": YR_TO_YOB.get(str(r[25]).strip(), 2004),
                "Height": ht_cm,
                "GP": gp, "MIN": round(mp, 1), "PTS": round(float(r[63]), 1),
                "FGM": round(fgm_pg, 2), "FGA": round(fga_pg, 2), "FG%": round(fgm_pg / fga_pg if fga_pg > 0 else 0.0, 3),
                "3PM": round(tpm_pg, 2), "3PA": round(tpa_pg, 2), "3P%": round(tp_pct, 3),
                "FTM": round(ftm_pg, 2), "FTA": round(fta_pg, 2), "FT%": round(ftm_pg / fta_pg if fta_pg > 0 else 0.0, 3),
                "OFF": round(float(r[57]), 1), "DEF": round(float(r[58]), 1), "TRB": round(float(r[59]), 1),
                "AST": round(ast, 1), "STL": round(float(r[61]), 1), "BLK": round(float(r[62]), 1),
                "TOV": round(tov, 1), "PF": round(pf, 1),
            })
        except Exception:
            continue
    return rows

# ==========================================
# 2. REALGM LEADERBOARD SCRAPER
# ==========================================
class RealGMFetcher:
    def __init__(self, use_browser=True, api_key=""):
        self.use_browser = use_browser
        self.api_key = api_key.strip()
        self.sb_ctx = None
        self.sb = None

    def __enter__(self):
        if self.use_browser:
            from seleniumbase import SB
            self.sb_ctx = SB(uc=True, headless=False)
            self.sb = self.sb_ctx.__enter__()
            self.sb.open("https://basketball.realgm.com/")
            time.sleep(3)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.sb_ctx:
            self.sb_ctx.__exit__(exc_type, exc_val, exc_tb)

    def get_html(self, url):
        if self.use_browser:
            self.sb.open(url)
            time.sleep(2.5)
            return self.sb.get_page_source()
        else:
            enc = urllib.parse.quote(url, safe="")
            api_url = f"https://api.scraperapi.com/?api_key={self.api_key}&url={enc}&render=true&premium=true"
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=75) as r:
                return r.read().decode("utf-8", errors="ignore")

def parse_realgm_leaderboard(html, app_league_name, source_label):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tbl in soup.find_all("table"):
        try:
            df = pd.read_html(io.StringIO(str(tbl)))[0]
            df.columns = [str(c).strip().upper() for c in df.columns]
            if not {"PLAYER", "TEAM", "GP", "MPG", "PPG"}.issubset(set(df.columns)):
                continue
            for _, r in df.iterrows():
                gp = int(float(r.get("GP", 0) or 0))
                mpg = float(r.get("MPG", 0) or 0)
                if gp < 5 or mpg < 10.0:
                    continue
                fgm, fga = float(r.get("FGM", 0) or 0), float(r.get("FGA", 0) or 0)
                tpm, tpa = float(r.get("3PM", 0) or 0), float(r.get("3PA", 0) or 0)
                ftm, fta = float(r.get("FTM", 0) or 0), float(r.get("FTA", 0) or 0)
                tp_pct = float(r.get("3P%", 0) or 0)
                treb = float(r.get("RPG", 0) or 0)
                ast = float(r.get("APG", 0) or 0)
                blk = float(r.get("BPG", 0) or 0)

                rows.append({
                    "Source": source_label,
                    "Player": str(r["PLAYER"]).strip(),
                    "Team": str(r["TEAM"]).strip(),
                    "Origin League": app_league_name,
                    "Conference": "",
                    "Position": infer_position(0, ast, tpa, tp_pct, treb, blk),
                    "YOB": 2002,   # Placeholder until individual profile inspected
                    "Height": 0,   # 0 = neutral 190cm in engine (no false undersized penalty)
                    "GP": gp, "MIN": round(mpg, 1), "PTS": round(float(r.get("PPG", 0) or 0), 1),
                    "FGM": round(fgm, 2), "FGA": round(fga, 2), "FG%": round(float(r.get("FG%", 0) or 0), 3),
                    "3PM": round(tpm, 2), "3PA": round(tpa, 2), "3P%": round(tp_pct, 3),
                    "FTM": round(ftm, 2), "FTA": round(fta, 2), "FT%": round(float(r.get("FT%", 0) or 0), 3),
                    "OFF": round(float(r.get("ORB", 0) or 0), 1),
                    "DEF": round(float(r.get("DRB", 0) or 0), 1),
                    "TRB": round(treb, 1), "AST": round(ast, 1),
                    "STL": round(float(r.get("SPG", 0) or 0), 1),
                    "BLK": round(blk, 1),
                    "TOV": round(float(r.get("TOV", 0) or 0), 1),
                    "PF": round(float(r.get("PF", 0) or 0), 1),
                })
            break
        except Exception:
            continue
    return rows

def discover_intl_league_urls(fetcher):
    """Fetches RealGM's international stats hub once and grabs exact /international/league/{id}/{slug} paths."""
    html = fetcher.get_html("https://basketball.realgm.com/international/league/1/Spanish-ACB/stats")
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=re.compile(r"/international/league/\d+/[^/]+/stats")):
        name = a.get_text().strip()
        m = re.search(r"(/international/league/\d+/[^/]+)", a["href"])
        if m and name:
            found[name] = "https://basketball.realgm.com" + m.group(1)
    return found

if __name__ == "__main__":
    all_rows = []
    if INCLUDE_NCAA_D1:
        all_rows.extend(build_torvik_pool(TORVIK_YEAR))

    need_realgm = INCLUDE_NCAA_D2 or INCLUDE_NCAA_D3 or bool(REALGM_TO_APP_LEAGUE)
    if need_realgm:
        if not USE_LOCAL_BROWSER and not SCRAPERAPI_KEY:
            raise ValueError("Set SCRAPERAPI_KEY or set USE_LOCAL_BROWSER = True.")

        with RealGMFetcher(use_browser=USE_LOCAL_BROWSER, api_key=SCRAPERAPI_KEY) as fetcher:
            if INCLUDE_NCAA_D2:
                for p in range(1, PAGES_PER_LEAGUE + 1):
                    print(f"Fetching NCAA DII page {p}...")
                    h = fetcher.get_html(f"https://basketball.realgm.com/ncaa-dii/stats/{REALGM_SEASON}/Averages/Qualified/All/Season/All/points/desc/{p}/")
                    all_rows.extend(parse_realgm_leaderboard(h, "NCAA D2 (All)", f"RealGM D2 {REALGM_SEASON}"))

            if INCLUDE_NCAA_D3:
                for p in range(1, PAGES_PER_LEAGUE + 1):
                    print(f"Fetching NCAA DIII page {p}...")
                    h = fetcher.get_html(f"https://basketball.realgm.com/ncaa-diii/stats/{REALGM_SEASON}/Averages/Qualified/All/Season/All/points/desc/{p}/")
                    all_rows.extend(parse_realgm_leaderboard(h, "NCAA D3", f"RealGM D3 {REALGM_SEASON}"))

            if REALGM_TO_APP_LEAGUE:
                intl_urls = discover_intl_league_urls(fetcher)
                for rgm_name, app_name in REALGM_TO_APP_LEAGUE.items():
                    base_url = intl_urls.get(rgm_name)
                    if not base_url:
                        print(f"Warning: could not auto-find URL for '{rgm_name}'")
                        continue
                    for p in range(1, PAGES_PER_LEAGUE + 1):
                        print(f"Fetching {rgm_name} ({app_name}) page {p}...")
                        url = f"{base_url}/stats/{REALGM_SEASON}/Averages/All/points/All/desc/{p}/Regular_Season"
                        h = fetcher.get_html(url)
                        parsed = parse_realgm_leaderboard(h, app_name, f"RealGM {REALGM_SEASON}")
                        all_rows.extend(parsed)
                        if len(parsed) < 90:  # Reached last page of this league
                            break

    out_df = pd.DataFrame(all_rows).drop_duplicates(subset=["Player", "Team", "Origin League"])
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nDone! Saved {len(out_df):,} players across {out_df['Origin League'].nunique()} leagues to {OUTPUT_CSV}.")
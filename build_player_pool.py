import io
import os
import re
import importlib
import urllib.parse
import urllib.request
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

# ==========================================
# CONFIGURATION
# ==========================================
SEASONS_TO_FETCH = [2025, 2026]      # 2025 = 2024-25, 2026 = 2025-26, 2027 = 2026-27
FORCE_REFRESH_SEASONS = []           # e.g. [2027] to re-scrape an in-progress season
PAGES_PER_LEAGUE = 3                 # Up to 300 players per international league
USE_LOCAL_BROWSER = True
SCRAPERAPI_KEY = ""
OUTPUT_CSV = "player_pool.csv"

INCLUDE_NCAA_D1 = True
INCLUDE_NCAA_D2 = True
INCLUDE_NCAA_D3 = True

# All 59 RealGM International Leagues matching your RAW_LEAGUE_DATA table
REALGM_TO_APP_LEAGUE = {
    # --- Spain & Top European ---
    "Spanish ACB": "Spain Liga ACB",
    "Spanish Primera FEB": "Spain Primera FEB",
    "Spanish Segunda FEB": "Spain Segunda FEB",
    "Spanish Liga U": "Spanish Liga U",
    "Euroleague": "EuroLeague",
    "Eurocup": "EuroCup",
    "Adriatic League Liga ABA": "Adriatic League (ABA)",
    # --- Western & Central Europe ---
    "French Jeep Elite": "France LNB Pro A",
    "French LNB Pro B": "France Pro B",
    "French NM1": "France NM1",
    "German BBL": "Germany BBL",
    "German Pro A": "Germany Pro A",
    "German Pro B": "Germany Pro B",
    "Italian Lega Basket Serie A": "Italy Lega Basket Serie A (LBA)",
    "Italian Serie A2 Basket": "Italy Lega A2",
    "BNXT League": "BNXT (Belgian/Top)",
    "Portuguese LPB": "Portugal LPB (Rest)",
    "Super League Basketball": "British Super League (SLB)",
    "Swiss LNA": "Swiss LNA",
    "Austrian A Bundesliga": "Austria 1 (BSL)",
    # --- Northern & Baltic ---
    "Lithuanian LKL": "Lithuania LKL",
    "Lithuanian NKL": "Lithuania NKL",
    "Estonian-Latvian Basketball League": "Estonia-Latvia (ELBL)",
    "Finnish Korisliiga": "Finland Korisliiga",
    "Swedish Basketligan": "Sweden Basketligan",
    "Danish Basketligaen": "Danish Basketligaen",
    "Norwegian BLNO": "Norway BLNO",
    # --- Eastern Europe & Mediterranean ---
    "Greek HEBA A1": "Greek Basket League (A1)",
    "Greek HEBA A2": "Greece Elite League (A2)",
    "Turkish TBL": "Turkey TBL (Div 2)",
    "Polish OBL": "Polish OBL",
    "Czech NBL": "Czech Rep NBL (Rest)",
    "Hungarian NBIA": "Hungary NBIA",
    "Romanian Divizia A": "Romania Liga Națională",
    "Bulgarian NBL": "Bulgaria NBL",
    "Serbian KLS": "Serbia KLS",
    "Croatian A-1 Liga": "Croatia A-1 (Premijer)",
    "Bosnian BiH Liga": "Bosnia (Prvenstvo BiH)",
    "Montenegrin Prva A Liga": "Montenegro Prva A",
    "Slovenian SKL": "Slovenia SKL",
    "Slovakian Extraliga": "Slovakia Extraliga",
    "Macedonian Superleague": "Macedonia Superleague",
    "Kosovo FBK": "Kosovo Superliga",
    "Cypriot Division A": "Cyprus Division A",
    "Georgian Super Liga": "Georgia Superleague",
    "Ukrainian Superleague": "Ukraine Superleague",
    # --- Americas, Asia, Africa, Oceania ---
    "Australian NBL": "Australia NBL",
    "New Zealand NBL": "New Zealand NBL",
    "Japanese B.League": "Japan B.League (B1)",
    "Chinese CBA": "China CBA",
    "Lebanese Division A": "Lebanon LBL (Div A)",
    "Basketball Africa League": "BAL (Africa)",
    "Argentinian Liga A": "Argentina LNB",
    "Brazilian NBB": "Brazil NBB",
    "Puerto Rican BSN": "Puerto Rico BSN",
    "Mexican LNBP": "Mexico LNBP (Rest)",
    "Mexican CIBACOPA": "Mexico CIBACOPA",
    "Venezuelan SLB": "Venezuela SPB",
    "Canadian Elite Basketball League": "Canada CEBL",
}

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

def safe_float(val, default=0.0):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    s = str(val).strip()
    if s in ("", "-", "—", ".", "nan", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default

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
    cache_file = f"torvik_cache_{year}.csv"
    print(f"[{year}] Fetching BartTorvik NCAA D1...", flush=True)
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    text = ""

    if year in FORCE_REFRESH_SEASONS or not os.path.exists(cache_file):
        try:
            from curl_cffi import requests as cf_requests
            resp = cf_requests.get(url, impersonate="chrome120", timeout=30)
            if resp.status_code == 200 and "<html" not in resp.text[:300].lower():
                text = resp.text
        except Exception:
            pass
        if not text:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    cand = r.read().decode("utf-8", errors="ignore")
                    if "<html" not in cand[:300].lower():
                        text = cand
            except Exception:
                pass
        if text:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(text)

    if not text and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            text = f.read()

    if not text:
        print(f"  -> WARNING: BartTorvik {year} unavailable.", flush=True)
        return []

    yr_map = {
        "Fr": year - 19,
        "So": year - 20,
        "Jr": year - 21,
        "Sr": year - 22,
    }

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
                "Season": int(year),
                "Source": f"BartTorvik {year}",
                "Player": str(r[0]).strip(),
                "Team": str(r[1]).strip(),
                "Origin League": TORVIK_CONF_TO_TIER.get(conf, "NCAA D1 (Low Major)"),
                "Conference": conf,
                "Position": infer_position(ht_cm, ast, tpa_pg, tp_pct, float(r[59]), float(r[62])),
                "YOB": yr_map.get(str(r[25]).strip(), year - 22),
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
    print(f"  -> Parsed {len(rows):,} NCAA D1 players ({year})", flush=True)
    return rows

class RealGMFetcher:
    def __init__(self, use_browser=True, api_key=""):
        self.use_browser = use_browser
        self.api_key = api_key.strip()
        self.sb = None
        self.first_stats_checked = False

    def __enter__(self):
        if self.use_browser:
            sb_cdp = importlib.import_module("seleniumbase.core.sb_cdp")
            profile_dir = str(Path("realgm_profile").resolve())
            first_setup = not Path("realgm_profile").exists()
            print("Launching Chrome in pure CDP mode (ad_block=True)...", flush=True)
            self.sb = sb_cdp.Chrome(
                "https://basketball.realgm.com/",
                headless=False,
                ad_block=True,
                user_data_dir=profile_dir
            )
            self.sb.sleep(3)
            self._dismiss_banners()
            if first_setup:
                input("\n>>> FIRST-TIME SETUP: Accept any Cookie/Cloudflare banner in Chrome, then press ENTER here...")
                self._dismiss_banners()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.sb:
            try:
                self.sb.quit()
            except Exception:
                pass

    def _dismiss_banners(self):
        js_click = """
        (() => {
            const sels = ['#onetrust-accept-btn-handler', 'button.fc-cta-consent', '.fc-button.fc-cta-consent', 'button[aria-label*="Accept" i]'];
            for (const s of sels) { const el = document.querySelector(s); if (el) { el.click(); return true; } }
            for (const b of document.querySelectorAll('button, a')) {
                const t = (b.innerText || '').trim().toLowerCase();
                if (['accept', 'accept all', 'agree', 'i agree', 'consent', 'aceptar', 'aceptar todo'].includes(t)) { b.click(); return true; }
            }
            return false;
        })()
        """
        try:
            self.sb.evaluate(js_click)
        except Exception:
            pass

    def _read_source(self):
        for fn in (
            lambda: self.sb.evaluate("document.documentElement.outerHTML"),
            lambda: self.sb.get_page_source(include_shadow_dom=False),
        ):
            try:
                html = fn()
                if html and "<body" in html.lower():
                    return html
            except Exception:
                continue
        return ""

    def get_html(self, url):
        if self.use_browser:
            self.sb.goto(url)
            self.sb.sleep(2.5)
            self._dismiss_banners()
            html = self._read_source()
            for _ in range(5):
                if "<table" in html.lower():
                    break
                self.sb.sleep(1)
                html = self._read_source()
            if not self.first_stats_checked:
                self.first_stats_checked = True
                if "<table" not in html.lower():
                    input("\n>>> If a Cookie/Captcha popup is visible in Chrome, clear it then press ENTER...")
                    self._dismiss_banners()
                    html = self._read_source()
            return html
        else:
            enc = urllib.parse.quote(url, safe="")
            api_url = f"https://api.scraperapi.com/?api_key={self.api_key}&url={enc}&render=true&premium=true"
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=75) as r:
                return r.read().decode("utf-8", errors="ignore")

    def sort_by_ppg_and_read(self):
        if not self.use_browser:
            return self._read_source()
        js_sort = """
        (() => {
            for (const el of document.querySelectorAll('table th, table th a')) {
                if ((el.innerText || '').trim().toUpperCase() === 'PPG') { el.click(); return true; }
            }
            return false;
        })()
        """
        try:
            self.sb.evaluate(js_sort)
            self.sb.sleep(1.5)
        except Exception:
            pass
        return self._read_source()

    def go_next_page(self, next_page_num):
        if not self.use_browser:
            return None
        js_next = f"""
        (() => {{
            const dtNext = document.querySelector('a.paginate_button.next:not(.disabled), li.next:not(.disabled) a');
            if (dtNext) {{
                const href = dtNext.getAttribute('href') || '';
                if (!href || href === '#' || href.startsWith('javascript')) {{ dtNext.click(); return 'CLICKED'; }}
                return dtNext.href;
            }}
            for (const a of document.querySelectorAll('.pagination a, .dataTables_paginate a, a')) {{
                if ((a.innerText || '').trim() === '{next_page_num}') {{
                    const href = a.getAttribute('href') || '';
                    if (!href || href === '#' || href.startsWith('javascript')) {{ a.click(); return 'CLICKED'; }}
                    return a.href;
                }}
            }}
            return '';
        }})()
        """
        try:
            res = self.sb.evaluate(js_next)
            if res == "CLICKED":
                self.sb.sleep(2.0)
                return self._read_source()
            elif isinstance(res, str) and res.startswith("http"):
                return self.get_html(res)
        except Exception:
            pass
        return None

def parse_realgm_leaderboard(html, app_league_name, source_label, season_year):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    raw_row_count = 0

    for tbl in soup.find_all("table"):
        try:
            df = pd.read_html(io.StringIO(str(tbl)))[0]
            df.columns = [str(c).strip().upper() for c in df.columns]
            if not {"PLAYER", "TEAM", "GP", "MPG", "PPG"}.issubset(set(df.columns)):
                continue

            raw_row_count = len(df)
            for _, r in df.iterrows():
                try:
                    gp = int(safe_float(r.get("GP")))
                    mpg = safe_float(r.get("MPG"))
                    if gp < 5 or mpg < 10.0:
                        continue
                    fgm, fga = safe_float(r.get("FGM")), safe_float(r.get("FGA"))
                    tpm, tpa = safe_float(r.get("3PM")), safe_float(r.get("3PA"))
                    ftm, fta = safe_float(r.get("FTM")), safe_float(r.get("FTA"))
                    tp_pct = safe_float(r.get("3P%"))
                    treb = safe_float(r.get("RPG"))
                    ast = safe_float(r.get("APG"))
                    blk = safe_float(r.get("BPG"))

                    rows.append({
                        "Season": int(season_year),
                        "Source": source_label,
                        "Player": str(r["PLAYER"]).strip(),
                        "Team": str(r["TEAM"]).strip(),
                        "Origin League": app_league_name,
                        "Conference": "",
                        "Position": infer_position(0, ast, tpa, tp_pct, treb, blk),
                        "YOB": int(season_year) - 24,
                        "Height": 0,
                        "GP": gp, "MIN": round(mpg, 1), "PTS": round(safe_float(r.get("PPG")), 1),
                        "FGM": round(fgm, 2), "FGA": round(fga, 2), "FG%": round(safe_float(r.get("FG%")), 3),
                        "3PM": round(tpm, 2), "3PA": round(tpa, 2), "3P%": round(tp_pct, 3),
                        "FTM": round(ftm, 2), "FTA": round(fta, 2), "FT%": round(safe_float(r.get("FT%")), 3),
                        "OFF": round(safe_float(r.get("ORB")), 1),
                        "DEF": round(safe_float(r.get("DRB")), 1),
                        "TRB": round(treb, 1), "AST": round(ast, 1),
                        "STL": round(safe_float(r.get("SPG")), 1),
                        "BLK": round(blk, 1),
                        "TOV": round(safe_float(r.get("TOV")), 1),
                        "PF": round(safe_float(r.get("PF")), 1),
                    })
                except Exception:
                    continue
            break
        except Exception:
            continue

    return rows, raw_row_count

def discover_intl_league_urls(fetcher):
    html = fetcher.get_html("https://basketball.realgm.com/international/league/1/Spanish-ACB/stats")
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    ignore_names = {"Stats", "Player Stats", "Team Stats", "News", "Teams", "Players", "Schedule", "Standings", "Leaders", "Scores", "Awards"}
    for tag in soup.find_all(["a", "option"]):
        href = tag.get("href") or tag.get("value") or ""
        m = re.search(r"(/international/league/\d+/[^/?#]+)", href)
        if m:
            name = tag.get_text().strip()
            if name and name not in ignore_names:
                found[name] = "https://basketball.realgm.com" + m.group(1)
    return found

if __name__ == "__main__":
    seasons_needed = [
        y for y in SEASONS_TO_FETCH
        if (y in FORCE_REFRESH_SEASONS) or not os.path.exists(f"pool_season_{y}.csv")
    ]

    if seasons_needed:
        need_realgm = INCLUDE_NCAA_D2 or INCLUDE_NCAA_D3 or bool(REALGM_TO_APP_LEAGUE)
        fetcher_ctx = RealGMFetcher(use_browser=USE_LOCAL_BROWSER, api_key=SCRAPERAPI_KEY) if need_realgm else None
        fetcher = fetcher_ctx.__enter__() if fetcher_ctx else None
        try:
            intl_urls = discover_intl_league_urls(fetcher) if (fetcher and REALGM_TO_APP_LEAGUE) else {}
            lower_lookup = {k.lower(): v for k, v in intl_urls.items()}

            for year in seasons_needed:
                print(f"\n========== BUILDING SEASON {year} ({year - 1}-{str(year)[-2:]}) ==========", flush=True)
                season_rows = []

                if INCLUDE_NCAA_D1:
                    season_rows.extend(build_torvik_pool(year))

                if fetcher and INCLUDE_NCAA_D2:
                    print(f"[{year}] Fetching NCAA DII...", flush=True)
                    h = fetcher.get_html(f"https://basketball.realgm.com/ncaa-dii/stats/{year}/Averages/Qualified/All/Season/All/points/desc/1/")
                    parsed, _ = parse_realgm_leaderboard(h, "NCAA D2 (All)", f"RealGM D2 {year}", year)
                    season_rows.extend(parsed)
                    print(f"  -> {len(parsed):,} NCAA DII players", flush=True)

                if fetcher and INCLUDE_NCAA_D3:
                    print(f"[{year}] Fetching NCAA DIII...", flush=True)
                    h = fetcher.get_html(f"https://basketball.realgm.com/ncaa-diii/stats/{year}/Averages/Qualified/All/Season/All/points/desc/1/")
                    parsed, _ = parse_realgm_leaderboard(h, "NCAA D3", f"RealGM D3 {year}", year)
                    season_rows.extend(parsed)
                    print(f"  -> {len(parsed):,} NCAA DIII players", flush=True)

                if fetcher and REALGM_TO_APP_LEAGUE:
                    for rgm_name, app_name in REALGM_TO_APP_LEAGUE.items():
                        base_url = intl_urls.get(rgm_name) or lower_lookup.get(rgm_name.lower())
                        if not base_url:
                            print(f"  -> Skipping '{rgm_name}' (URL not found)", flush=True)
                            continue

                        seen_players = set()
                        h = fetcher.get_html(f"{base_url}/stats/{year}/Averages")
                        h = fetcher.sort_by_ppg_and_read()

                        for p in range(1, PAGES_PER_LEAGUE + 1):
                            parsed, raw_cnt = parse_realgm_leaderboard(h, app_name, f"RealGM {year}", year)
                            new_rows = [r for r in parsed if (r["Player"], r["Team"]) not in seen_players]
                            for r in new_rows:
                                seen_players.add((r["Player"], r["Team"]))
                            season_rows.extend(new_rows)
                            if p >= PAGES_PER_LEAGUE or raw_cnt < 95 or len(new_rows) == 0:
                                break
                            h = fetcher.go_next_page(p + 1)
                            if not h:
                                break

                        print(f"[{year}] {rgm_name} -> {len(seen_players)} players", flush=True)

                s_df = pd.DataFrame(season_rows).drop_duplicates(subset=["Season", "Player", "Team", "Origin League"])
                s_df.to_csv(f"pool_season_{year}.csv", index=False)
                print(f"Saved pool_season_{year}.csv ({len(s_df):,} players)", flush=True)
        finally:
            if fetcher_ctx:
                fetcher_ctx.__exit__(None, None, None)

    # Merge all selected season files into player_pool.csv
    dfs = [pd.read_csv(f"pool_season_{y}.csv") for y in SEASONS_TO_FETCH if os.path.exists(f"pool_season_{y}.csv")]
    if dfs:
        merged = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["Season", "Player", "Team", "Origin League"])
        merged.to_csv(OUTPUT_CSV, index=False)
        print(f"\nALL DONE! Merged {len(merged):,} player-seasons across {merged['Origin League'].nunique()} leagues into {OUTPUT_CSV}.")
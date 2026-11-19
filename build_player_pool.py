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
	"Polish OBL": "Polish OBL",
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
	cache_file = f"torvik_cache_{year}.csv"
	print(f"Fetching BartTorvik NCAA D1 ({year})...", flush=True)
	url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
	text = ""

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
	elif os.path.exists(cache_file):
		print(f"  -> Live BartTorvik unavailable; using cached {cache_file}", flush=True)
		with open(cache_file, "r", encoding="utf-8") as f:
			text = f.read()
	else:
		print("  -> WARNING: BartTorvik returned 0 rows (blocked/throttled).", flush=True)
		return []

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
	print(f"  -> Parsed {len(rows):,} NCAA D1 players from BartTorvik", flush=True)
	return rows

# ==========================================
# 2. REALGM LEADERBOARD SCRAPER
# ==========================================
class RealGMFetcher:
	def __init__(self, use_browser=True, api_key=""):
		self.use_browser = use_browser
		self.api_key = api_key.strip()
		self.sb = None
		self.first_stats_checked = False

	def __enter__(self):
		if self.use_browser:
			import importlib
			from pathlib import Path
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
				input("\n>>> FIRST-TIME SETUP: In Chrome, click 'Accept' on any Cookie/Privacy banner (and any Cloudflare box), then press ENTER here...")
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
			const selectors = [
				'#onetrust-accept-btn-handler',
				'button.fc-cta-consent',
				'.fc-button.fc-cta-consent',
				'button[aria-label*="Accept" i]',
				'button[aria-label*="Agree" i]',
				'button[aria-label*="Consent" i]'
			];
			for (const sel of selectors) {
				const el = document.querySelector(sel);
				if (el) { el.click(); return true; }
			}
			const btns = Array.from(document.querySelectorAll('button, a'));
			for (const b of btns) {
				const txt = (b.innerText || '').trim().toLowerCase();
				if (['accept', 'accept all', 'agree', 'i agree', 'consent', 'aceptar', 'aceptar todo'].includes(txt)) {
					b.click();
					return true;
				}
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
			print(f"  -> Navigating to {url}", flush=True)
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
					input("\n>>> No table detected yet—click Accept/Verify in Chrome if needed, then press ENTER here...")
					self._dismiss_banners()
					html = self._read_source()
			print(f"  -> Read {len(html):,} chars", flush=True)
			return html
		else:
			enc = urllib.parse.quote(url, safe="")
			api_url = f"https://api.scraperapi.com/?api_key={self.api_key}&url={enc}&render=true&premium=true"
			req = urllib.request.Request(api_url)
			with urllib.request.urlopen(req, timeout=75) as r:
				return r.read().decode("utf-8", errors="ignore")
	def sort_by_ppg_and_read(self):
		"""Clicks the PPG header if present so top scorers come first, then reads HTML."""
		if not self.use_browser:
			return self._read_source()
		js_sort_ppg = """
		(() => {
			const headers = Array.from(document.querySelectorAll('table th, table th a'));
			for (const el of headers) {
				if ((el.innerText || '').trim().toUpperCase() === 'PPG') {
					el.click();
					return true;
				}
			}
			return false;
		})()
		"""
		try:
			self.sb.evaluate(js_sort_ppg)
			self.sb.sleep(1.5)
		except Exception:
			pass
		return self._read_source()

	def go_next_page(self, next_page_num):
		"""Advances RealGM table pagination (handles both DataTables JS buttons and <a> page links)."""
		if not self.use_browser:
			return None
		js_next = f"""
		(() => {{
			// 1. Try DataTables Next button (if not disabled)
			const dtNext = document.querySelector('a.paginate_button.next:not(.disabled), li.next:not(.disabled) a');
			if (dtNext) {{
				const href = dtNext.getAttribute('href') || '';
				if (!href || href === '#' || href.startsWith('javascript')) {{
					dtNext.click();
					return 'CLICKED';
				}}
				return dtNext.href;
			}}
			// 2. Try numbered page link matching next_page_num
			const links = Array.from(document.querySelectorAll('.pagination a, .dataTables_paginate a, a'));
			for (const a of links) {{
				if ((a.innerText || '').trim() === '{next_page_num}') {{
					const href = a.getAttribute('href') || '';
					if (!href || href === '#' || href.startsWith('javascript')) {{
						a.click();
						return 'CLICKED';
					}}
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

def parse_realgm_leaderboard(html, app_league_name, source_label):
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
						"Source": source_label,
						"Player": str(r["PLAYER"]).strip(),
						"Team": str(r["TEAM"]).strip(),
						"Origin League": app_league_name,
						"Conference": "",
						"Position": infer_position(0, ast, tpa, tp_pct, treb, blk),
						"YOB": 2002,
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

	print(f"  -> Parsed {len(rows)} qualified players (from {raw_row_count} table rows)", flush=True)
	return rows, raw_row_count

def discover_intl_league_urls(fetcher):
	"""Fetches RealGM's international stats page and grabs ALL /international/league/{id}/{slug} URLs."""
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
					parsed, _ = parse_realgm_leaderboard(h, "NCAA D2 (All)", f"RealGM D2 {REALGM_SEASON}")
					all_rows.extend(parsed)

			if INCLUDE_NCAA_D3:
				for p in range(1, PAGES_PER_LEAGUE + 1):
					print(f"Fetching NCAA DIII page {p}...")
					h = fetcher.get_html(f"https://basketball.realgm.com/ncaa-diii/stats/{REALGM_SEASON}/Averages/Qualified/All/Season/All/points/desc/{p}/")
					parsed, _ = parse_realgm_leaderboard(h, "NCAA D3", f"RealGM D3 {REALGM_SEASON}")
					all_rows.extend(parsed)

			if REALGM_TO_APP_LEAGUE:
				intl_urls = discover_intl_league_urls(fetcher)
				lower_lookup = {k.lower(): v for k, v in intl_urls.items()}
				missing_names = []

				for rgm_name, app_name in REALGM_TO_APP_LEAGUE.items():
					base_url = intl_urls.get(rgm_name) or lower_lookup.get(rgm_name.lower())
					if not base_url:
						missing_names.append(rgm_name)
						continue

					seen_league_players = set()
					print(f"Fetching {rgm_name} ({app_name}) page 1...")
					clean_url = f"{base_url}/stats/{REALGM_SEASON}/Averages"
					h = fetcher.get_html(clean_url)
					h = fetcher.sort_by_ppg_and_read()

					for p in range(1, PAGES_PER_LEAGUE + 1):
						parsed, raw_cnt = parse_realgm_leaderboard(h, app_name, f"RealGM {REALGM_SEASON}")
						new_rows = [r for r in parsed if (r["Player"], r["Team"]) not in seen_league_players]
						for r in new_rows:
							seen_league_players.add((r["Player"], r["Team"]))
						all_rows.extend(new_rows)
						print(f"  -> Added {len(new_rows)} new players (league total: {len(seen_league_players)})", flush=True)

						if p >= PAGES_PER_LEAGUE or raw_cnt < 95 or len(new_rows) == 0:
							break

						print(f"Fetching {rgm_name} ({app_name}) page {p + 1}...")
						h = fetcher.go_next_page(p + 1)
						if not h:
							break

				if missing_names:
					print("\nUnmatched league names:", missing_names)
					print("Exact names found on RealGM:", sorted(intl_urls.keys()))

	out_df = pd.DataFrame(all_rows).drop_duplicates(subset=["Player", "Team", "Origin League"])
	out_df.to_csv(OUTPUT_CSV, index=False)
	print(f"\nDone! Saved {len(out_df):,} players across {out_df['Origin League'].nunique()} leagues to {OUTPUT_CSV}.")
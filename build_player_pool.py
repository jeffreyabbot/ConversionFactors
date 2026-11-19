import re
import pandas as pd

try:
    from curl_cffi import requests as cf_requests
except ImportError:
    cf_requests = None
    import urllib.request

TORVIK_YEAR = 2026  # Completed 2025-26 season; switch to 2027 once mid-season GP is high enough
OUTPUT_CSV = "player_pool.csv"

TORVIK_CONF_TO_TIER = {
    # High Major
    "ACC": "NCAA D1 (High Major)", "A10": "NCAA D1 (High Major)",
    "B12": "NCAA D1 (High Major)", "BE": "NCAA D1 (High Major)",
    "B10": "NCAA D1 (High Major)", "MWC": "NCAA D1 (High Major)",
    "P12": "NCAA D1 (High Major)", "SEC": "NCAA D1 (High Major)",
    # Mid Major
    "Amer": "NCAA D1 (Mid Major)", "BSky": "NCAA D1 (Mid Major)",
    "BW": "NCAA D1 (Mid Major)", "CAA": "NCAA D1 (Mid Major)",
    "CUSA": "NCAA D1 (Mid Major)", "Ivy": "NCAA D1 (Mid Major)",
    "MAC": "NCAA D1 (Mid Major)", "MVC": "NCAA D1 (Mid Major)",
    "WCC": "NCAA D1 (Mid Major)", "WAC": "NCAA D1 (Mid Major)",
    # Low Major
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
    feet, inches = int(m.group(1)), int(m.group(2))
    return round((feet * 12 + inches) * 2.54)

def infer_position(ht_cm, ast, tpa_pg, tp_pct):
    if ht_cm >= 201:
        return "Bigs (C/PF)"
    if ast >= 3.0 and ht_cm <= 193:
        return "Slashers/PGs"
    if tpa_pg >= 3.0 and tp_pct >= 0.32:
        return "Shooters (SG/SF)"
    return "Slashers/PGs" if ht_cm <= 191 else "Slashers / D(Non-Shooters)"

def fetch_torvik_df(year):
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    if cf_requests:
        resp = cf_requests.get(url, impersonate="chrome120", timeout=30)
        resp.raise_for_status()
        text = resp.text
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", errors="ignore")
    import io
    return pd.read_csv(io.StringIO(text), header=None)

def build_torvik_pool(year):
    raw = fetch_torvik_df(year)
    rows = []

    for _, r in raw.iterrows():
        try:
            name = str(r[0]).strip()
            team = str(r[1]).strip()
            conf = str(r[2]).strip()
            gp = int(float(r[3]))
            mp = float(r[54])
            if gp < 8 or mp < 10.0:
                continue

            league_tier = TORVIK_CONF_TO_TIER.get(conf, "NCAA D1 (Low Major)")
            ht_cm = ht_to_cm(r[26])
            yob = YR_TO_YOB.get(str(r[25]).strip(), 2004)

            ftm_pg = float(r[13]) / gp
            fta_pg = float(r[14]) / gp
            twom_pg = float(r[16]) / gp
            twoa_pg = float(r[17]) / gp
            tpm_pg = float(r[19]) / gp
            tpa_pg = float(r[20]) / gp

            fgm_pg = twom_pg + tpm_pg
            fga_pg = twoa_pg + tpa_pg
            fg_pct = fgm_pg / fga_pg if fga_pg > 0 else 0.0
            tp_pct = tpm_pg / tpa_pg if tpa_pg > 0 else 0.0
            ft_pct = ftm_pg / fta_pg if fta_pg > 0 else 0.0

            oreb = float(r[57])
            dreb = float(r[58])
            treb = float(r[59])
            ast = float(r[60])
            stl = float(r[61])
            blk = float(r[62])
            pts = float(r[63])

            ast_tov = float(r[35]) if pd.notna(r[35]) else 0.0
            to_per = float(r[12]) if pd.notna(r[12]) else 15.0
            tov = (ast / ast_tov) if ast_tov > 0 else (to_per / 100.0) * (fga_pg + 0.44 * fta_pg)
            pfr = float(r[30]) if pd.notna(r[30]) else 3.0
            pf = (pfr / 40.0) * mp

            pos = infer_position(ht_cm, ast, tpa_pg, tp_pct)

            rows.append({
                "Source": f"BartTorvik {year}",
                "Player": name,
                "Team": team,
                "Origin League": league_tier,
                "Conference": conf,
                "Position": pos,
                "YOB": yob,
                "Height": ht_cm,
                "GP": gp,
                "MIN": round(mp, 1),
                "PTS": round(pts, 1),
                "FGM": round(fgm_pg, 2),
                "FGA": round(fga_pg, 2),
                "FG%": round(fg_pct, 3),
                "3PM": round(tpm_pg, 2),
                "3PA": round(tpa_pg, 2),
                "3P%": round(tp_pct, 3),
                "FTM": round(ftm_pg, 2),
                "FTA": round(fta_pg, 2),
                "FT%": round(ft_pct, 3),
                "OFF": round(oreb, 1),
                "DEF": round(dreb, 1),
                "TRB": round(treb, 1),
                "AST": round(ast, 1),
                "STL": round(stl, 1),
                "BLK": round(blk, 1),
                "TOV": round(tov, 1),
                "PF": round(pf, 1),
            })
        except Exception:
            continue

    return pd.DataFrame(rows)

if __name__ == "__main__":
    df_pool = build_torvik_pool(TORVIK_YEAR)
    df_pool.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {len(df_pool)} players to {OUTPUT_CSV}")
    print(df_pool.head(3)[["Player", "Team", "Origin League", "Height", "GP", "MIN", "PTS", "TRB", "AST"]])
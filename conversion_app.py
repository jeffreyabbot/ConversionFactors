import streamlit as st
import pandas as pd
import io
import os
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup  # type: ignore
import re
import base64

# Try to import curl_cffi to bypass Cloudflare's TLS fingerprint blocks
try:
	import importlib
	curl_requests = importlib.import_module("curl_cffi.requests")
except ImportError:
	curl_requests = None
# ==========================================
# GLOBAL FALLBACKS FOR STATIC ANALYSIS SAFETY
# ==========================================
player_name = "Random Player"
orig_team = "Unknown Team"
yob = 2005
height = 0
gp = 26
position = "Slashers/PGs"
orig_league_name = "Spain Segunda FEB"
dest_league_name = "Spain Segunda FEB"
target_role = "D (Glue / Specialist)"
target_min = 14.0
selected_modifiers = []
role_mod = 1.15
# ==========================================
# 0. ROBUST HTTP UTILITY HELPER
# ==========================================

def calculate_weighted_shooting(history, current_stats, source="FEB", skip_label=None):
    curr_m = current_stats.get("FGM", 0); curr_a = current_stats.get("FGA", 0)
    curr_3m = current_stats.get("3PM", 0); curr_3a = current_stats.get("3PA", 0)

    hist_m, hist_a, hist_3m, hist_3a = 0, 0, 0, 0
    seasons_counted = 0

    if source == "FEB":
        for row in history:
            # SKIP logic: If this row's label matches the one we are importing, skip it
            if skip_label and row.get("label") == skip_label:
                continue
            
            stats = row.get("stats", {})
            def split_feb(v):
                if "-" in str(v):
                    p = str(v).split("-")
                    return float(p[0]), float(p[1])
                return 0.0, 0.0
            m2, a2 = split_feb(stats.get("T2", "0-0"))
            m3, a3 = split_feb(stats.get("T3", "0-0"))
            
            hist_m += (m2 + m3); hist_a += (a2 + a3)
            hist_3m += m3; hist_3a += a3
            seasons_counted += 1
            
    else: # RealGM
        for row in history:
            try:
                def clean_rgm(val):
                    return float(str(val).replace(",", "").strip()) if val else 0.0
                ta = clean_rgm(row.get("FGA", 0))
                if ta < 15: continue 
                
                tm = clean_rgm(row.get("FGM", row.get("FG", 0)))
                # RealGM skip logic (using volume as fallback)
                if tm == current_stats.get("FGM_TOTAL", -1): continue

                hist_m += tm; hist_a += ta
                hist_3m += clean_rgm(row.get("3PM", row.get("3FG", 0)))
                hist_3a += clean_rgm(row.get("3PA", row.get("3FGA", 0)))
                seasons_counted += 1
            except: continue

    def blend(c_m, c_a, h_m, h_a):
        c_pct = c_m / c_a if c_a > 0 else 0
        h_pct = h_m / h_a if h_a > 0 else c_pct
        if h_a == 0: return c_pct, "No other career volume found"
        final = (c_pct * 0.60) + (h_pct * 0.40)
        return final, f"Blended {c_pct:.1%} (Selected) with {h_pct:.1%} (Career: {int(h_a)} shots)"

    fg_val, fg_msg = blend(curr_m, curr_a, hist_m, hist_a)
    tp_val, tp_msg = blend(curr_3m, curr_3a, hist_3m, hist_3a)

    return {
        "weighted_fg%": fg_val, "weighted_3p%": tp_val,
        "debug": f"Source: {source} | History Seasons: {seasons_counted} | FG: {fg_msg} | 3P: {tp_msg}"
    }
# =====================
# GLOBAL STYLING HELPER
# =====================
def highlight_scouting_outliers(row):
    metric = str(row.name)
    styles = [''] * len(row)
    
    def get_text_style(val, target_high, target_low):
        # Red for Elite/High, Blue for Below Average/Low
        if val >= target_high:
            return "color: #E63946; font-weight: 900;" # Heavy Red
        if val <= target_low:
            return "color: #2E86C1; font-weight: 900;" # Heavy Blue
        return "color: #1F2937; font-weight: normal;" # Default Grey/Black

    # Extract numeric value from the PROJECTION column
    try:
        # Clean the string (remove %, ±, etc) to get a pure number for comparison
        clean_val = str(row["BASE PROJECTION"]).replace("%", "").replace("±", "").strip()
        base_val = float(clean_val)
    except:
        return styles

    # Benchmarks for bold/color logic
    thresholds = {
        "PTS": (14.0, 5.0),
        "REB": (7.5, 2.5),
        "AST": (4.5, 1.5),
        "VAL": (15.0, 6.0),
        "2P%": (55.0, 42.0),
        "3P%": (38.0, 28.0),
        "FT%": (82.0, 65.0),
        "STL": (1.5, 0.6),
        "BLK": (1.0, 0.2),
        "TO": (1.2, 3.0) # Low is better
    }

    active_style = ""
    for key, (hi, lo) in thresholds.items():
        if key in metric:
            if key == "TO": # Inverse logic: Low TO is Red (Elite), High TO is Blue (Poor)
                if base_val <= hi: active_style = "color: #E63946; font-weight: 900;"
                elif base_val >= lo: active_style = "color: #2E86C1; font-weight: 900;"
            else:
                active_style = get_text_style(base_val, hi, lo)
            break

    if active_style:
        for i, col in enumerate(row.index):
            # Apply color and bold only to the three numeric columns
            if col in ["Floor", "BASE PROJECTION", "Ceiling"]:
                styles[i] = active_style
                
    return styles
def fetch_html_content(url, headers=None, data=None, method="GET"):
	"""
	HTTP helper that uses curl_cffi (if available) to impersonate Chrome and bypass anti-bot blocks.
	Falls back to standard urllib if curl_cffi is missing.
	"""
	if not headers:
		headers = {
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
			"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
			"Accept-Language": "en-US,en;q=0.9",
			"Connection": "keep-alive",
			"Upgrade-Insecure-Requests": "1"
		}

	if curl_requests:
		try:
			if method.upper() == "POST":
				# Convert urlencoded bytes back to a dictionary if passed from legacy FEB flow
				if isinstance(data, bytes):
					data_str = data.decode('utf-8', errors='ignore')
					data = dict(urllib.parse.parse_qsl(data_str))
				response = curl_requests.post(url, headers=headers, data=data, impersonate="chrome120", timeout=12)
			else:
				response = curl_requests.get(url, headers=headers, impersonate="chrome120", timeout=12)
				
			if response.status_code == 200:
				return response.text, response.url, None
			else:
				return None, None, f"HTTP Error {response.status_code}"
		except Exception as e:
			return None, None, f"Fetch failed: {str(e)}"
	else:
		# Fallback to legacy urllib
		try:
			req = urllib.request.Request(url, headers=headers, data=data, method=method)
			with urllib.request.urlopen(req, timeout=12) as response:
				html_text = response.read().decode('utf-8', errors='ignore')
				final_url = response.geturl()
				return html_text, final_url, None
		except Exception as e:
			return None, None, f"{str(e)} (Tip: Install 'curl_cffi' to bypass Cloudflare blocks)"

# Set page layout
st.set_page_config(layout="wide", page_title="RealGM Stats Conversor")

# ==========================================
# 1. INJECT CUSTOM CSS THEME
# ==========================================
st.markdown(
	"""
	<style>
	/* Main Background & Text Color */
	.stApp {
		background-color: #FFFFFF;
		color: #1F2937;
	}
	
	/* Dark Sidebar Styling */
	[data-testid="stSidebar"] {
		background-color: #11152C !important;
	}
	
	/* Style only specific text headings inside the sidebar */
	[data-testid="stSidebar"] h1, 
	[data-testid="stSidebar"] h2, 
	[data-testid="stSidebar"] h3, 
	[data-testid="stSidebar"] h4, 
	[data-testid="stSidebar"] h5, 
	[data-testid="stSidebar"] h6 {
		color: #FFFFFF !important;
	}
	
	/* Style widget labels (like selectbox and input labels) */
	[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
		color: #FFFFFF !important;
		font-weight: 600 !important;
	}
	
	/* Safely style radio and checkbox labels white without breaking native vertical flex alignment */
	[data-testid="stSidebar"] div[data-testid="stRadio"] label [data-testid="stMarkdownContainer"] p,
	[data-testid="stSidebar"] div[data-testid="stCheckbox"] label [data-testid="stMarkdownContainer"] p {
		color: #FFFFFF !important;
		margin: 0 !important;
		padding-left: 5px !important;
	}
	
	/* Input field background inside dark sidebar */
	[data-testid="stSidebar"] input {
		background-color: #1E2540 !important;
		color: #FFFFFF !important;
		border: 1px solid #3B4B7A !important;
	}
	
	/* Cohesive Dark Selectboxes inside the Sidebar */
	[data-testid="stSidebar"] div[data-baseweb="select"] > div {
		background-color: #1E2540 !important;
		color: #FFFFFF !important;
		border: 1px solid #3B4B7A !important;
	}
	
	/* Completely hide/shrink selectbox input fields in the sidebar to eradicate any caret cursors/boxes */
	[data-testid="stSidebar"] div[data-baseweb="select"] input {
		width: 0px !important;
		height: 0px !important;
		padding: 0px !important;
		opacity: 0 !important;
		pointer-events: none !important;
		position: absolute !important;
	}
	
	/* Ensure the selectbox dropdown arrow is white */
	[data-testid="stSidebar"] div[data-baseweb="select"] svg {
		fill: #FFFFFF !important;
	}
	
	/* Ensure the dropdown menu option lists are dark */
	div[role="listbox"] {
		background-color: #1E2540 !important;
	}
	div[role="listbox"] ul li {
		background-color: #1E2540 !important;
		color: #FFFFFF !important;
	}
	div[role="listbox"] ul li:hover {
		background-color: #3B4B7A !important;
		color: #FFFFFF !important;
	}
	
	/* Muted Sidebar Captions - High Legibility Gray-Blue */
	[data-testid="stSidebar"] .stCaptionContainer,
	[data-testid="stSidebar"] .stCaptionContainer p {
		color: #8E9AA6 !important;
	}
	/* Responsive stacking for columns on standard laptops and smaller screens */
	@media (max-width: 1350px) {
		div[data-testid="stHorizontalBlock"] {
			flex-direction: column !important;
		}
		/* Uses the direct child selector to override Streamlit's React inline styling percentages */
		div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
			width: 100% !important;
			min-width: 100% !important;
			max-width: 100% !important;
			margin-bottom: 20px !important;
		}
	}
	</style>
	""",
	unsafe_allow_html=True
)

# ==========================================
# 2. INITIALIZE SESSION STATE (MEMORY)
# ==========================================
if "view_mode" not in st.session_state:
	st.session_state.view_mode = "Home"
if "p_player_name" not in st.session_state:
	st.session_state.p_player_name = "Roger Fàbrega"
if "p_orig_team" not in st.session_state:
	st.session_state.p_orig_team = "Unknown Team"
if "p_yob" not in st.session_state:
	st.session_state.p_yob = 2005
if "p_height" not in st.session_state:
	st.session_state.p_height = 185
if "p_gp" not in st.session_state:
	st.session_state.p_gp = 26
if "raw_stats" not in st.session_state:
	st.session_state.raw_stats = {
		"MIN": 23.5, "PTS": 8.6, "FGM": 2.9, "FGA": 7.1, "FG%": 0.408,
		"3PM": 1.0, "3PA": 3.2, "3P%": 0.313,
		"FTM": 1.7, "FTA": 2.7, "FT%": 0.630,
		"OFF": 0.6, "DEF": 3.9, "TRB": 4.5, "AST": 6.2, "STL": 1.2, "BLK": 0.0, "TOV": 1.9,
		"PF": 1.9
	}
if "feb_parsed_profile" not in st.session_state:
	st.session_state.feb_parsed_profile = None

# Initialize persistent session variables cleanly
if "p_orig_league_name" not in st.session_state:
	st.session_state.p_orig_league_name = "Spain Segunda FEB"
if "p_dest_league_name" not in st.session_state:
	st.session_state.p_dest_league_name = "Spain Primera FEB"
if "p_target_role" not in st.session_state:
	st.session_state.p_target_role = "D (Glue / Specialist)"
if "p_target_min" not in st.session_state:
	st.session_state.p_target_min = 14.0
if "p_position" not in st.session_state:
	st.session_state.p_position = "Slashers/PGs"
if "p_selected_modifiers" not in st.session_state:
	st.session_state.p_selected_modifiers = []
if "p_role_mod" not in st.session_state:
	st.session_state.p_role_mod = 1.15
if "shortlist" not in st.session_state:
	st.session_state.shortlist = []
if "p_variability" not in st.session_state:
	st.session_state.p_variability = 0.20
if "p_orig_team_quality" not in st.session_state:
	st.session_state.p_orig_team_quality = "Mid-Table (Default)"
# ==========================================
# 3. RAW LEAGUE DATASET
# ==========================================
RAW_LEAGUE_DATA = """League	Tier	Rate	Bigs (C/PF)	Shooters (SG/SF)	Slashers / D(Non-Shooters)	Slashers/PGs	Rebounds	2P% Modifier	3P% Modifier	TO Modifier	CS	T High	T Low	Pace	Pace Factor	STL Modifier	BLK Modifier
Angola UNITEL	6	0,70	0,75	0,70	0,70	0,75	0,75	0,85	0,90	1,15	Estimated	-0,25	0,25	74,00	1,01	0,90	0,90
Argentina LNB	4	1,09	1,10	1,15	1,15	1,10	1,05	1,05	1,00	1,00	Medium	-0,10	0,10	71,50	1,05	1,05	1,00
Australia NBL	3	1,24	1,40	1,35	1,40	1,30	1,05	1,15	1,05	0,85	Very Low	-0,20	0,20	82,00	0,91	0,90	0,95
Austria 1 (BSL)	5	1,02	1,05	1,05	1,05	1,05	0,95	0,98	0,98	1,10	Very Low	-0,20	0,20	74,50	1,01	0,90	0,85
Austria B2L	7	0,55	0,40	0,50	0,45	0,55	0,60	0,70	0,65	1,30	Very Low	-0,20	0,20	78,00	0,96	0,75	0,65
BAL (Africa)	5	0,98	1,00	1,00	1,00	1,00	0,75	1,10	1,00	1,10	Medium	-0,10	0,10	76,00	0,99	0,95	1,05
BNXT (Belgian/Top)	4	1,13	1,10	1,20	1,10	1,10	1,00	1,15	1,25	1,00	Low	-0,15	0,15	76,00	0,99	0,95	1,00
BNXT (Dutch Bottom)	5	1,02	0,90	1,00	0,95	0,95	0,95	1,15	1,25	1,10	Low	-0,15	0,15	79,00	0,95	0,90	0,85
Bosnia (Prvenstvo BiH)	5	1,01	1,00	1,00	1,00	1,00	1,00	0,95	1,10	1,15	Medium	-0,10	0,10	72,00	1,04	0,95	0,95
Brazil NBB	5	1,04	1,10	1,00	1,00	1,00	1,15	1,05	1,00	1,00	Low	-0,15	0,15	76,00	0,99	0,95	1,00
British Super League (SLB)	5	1,01	1,05	1,10	1,05	1,05	0,90	0,95	1,00	1,00	Medium	-0,10	0,10	79,00	0,95	0,85	0,85
Bulgaria NBL	5	0,99	1,05	1,00	1,00	1,00	0,90	0,95	1,00	1,20	Low	-0,15	0,15	76,00	0,99	0,90	0,85
Canada BSL	6	0,95	1,00	0,95	0,95	0,94	1,00	0,90	0,90	1,10	Very Low	-0,20	0,20	78,00	0,96	0,85	0,90
Canada CEBL	5	1,01	1,10	1,05	1,10	1,05	1,20	0,90	0,70	1,10	Low	-0,15	0,15	78,00	0,96	0,90	0,90
Canada NBL	6	0,94	0,95	0,90	0,90	1,10	1,10	0,85	0,80	1,15	Very Low	-0,20	0,20	78,00	0,96	0,90	0,90
Chile LNB	7	0,79	0,70	0,80	0,70	0,75	0,80	0,85	0,90	1,20	Estimated	-0,25	0,25	72,00	1,04	0,90	0,85
China CBA	3	1,20	1,20	1,20	1,20	1,20	1,20	1,20	1,20	1,00	Very Low	-0,20	0,20	78,00	0,96	1,00	1,05
Croatia A-1 (Premijer)	5	0,95	1,00	0,95	0,90	0,90	0,90	1,05	0,95	1,05	Medium	-0,10	0,10	72,00	1,04	1,00	0,95
Cyprus Division A	6	0,85	0,90	0,85	0,85	0,85	0,90	0,90	0,95	1,15	Estimated	-0,25	0,25	74,00	1,01	0,90	0,85
Czech Rep NBL (Rest)	6	0,87	0,85	0,85	0,85	0,85	0,85	0,90	0,95	1,15	Very Low	-0,20	0,20	72,00	1,04	0,90	0,90
Czech Rep NBL (Top 4)	5	1,00	1,00	1,00	1,00	1,00	1,00	1,00	1,00	1,00	Very Low	-0,20	0,20	72,00	1,04	1,00	1,00
Danish Basketligaen	6	0,81	0,80	0,80	0,80	0,80	0,65	0,95	0,85	1,15	Medium	-0,10	0,10	77,00	0,97	0,85	0,80
Estonia-Latvia (ELBL)	5	0,95	1,00	1,00	1,00	1,00	0,70	0,95	1,00	1,05	Medium	-0,10	0,10	75,50	0,99	0,95	0,90
EuroCup	2	1,60	1,80	1,65	1,78	1,78	1,30	1,10	1,10	0,80	High	-0,05	0,05	74,00	1,01	1,12	1,12
EuroLeague	1	1,85	2,10	1,90	2,10	2,10	1,60	1,10	1,10	0,75	High	-0,05	0,05	71,50	1,05	1,15	1,15
Finland Korisliiga	6	0,98	1,00	1,10	1,05	0,90	0,95	0,98	0,90	1,20	Medium	-0,10	0,10	78,00	0,96	0,90	0,85
France LNB Pro A	2	1,54	1,78	1,62	1,72	1,72	1,28	1,08	1,08	0,82	High	-0,05	0,05	75,00	1,00	1,10	1,12
France NM1	5	1,02	1,00	1,00	1,00	1,00	1,25	0,95	0,95	1,05	Medium	-0,10	0,10	72,50	1,03	1,00	1,10
France Pro B	4	1,19	1,20	1,20	1,20	1,20	1,55	1,00	1,00	0,85	Medium	-0,10	0,10	75,00	1,00	1,00	1,15
Georgia Superleague	6	0,93	0,95	1,00	1,00	1,00	0,75	0,85	0,95	1,15	Low	-0,15	0,15	74,00	1,01	0,90	0,90
Germany BBL	2	1,50	1,72	1,58	1,68	1,68	1,24	1,08	1,08	0,85	High	-0,05	0,05	76,00	0,99	1,10	1,10
Germany Pro A	5	0,97	1,05	0,95	0,95	0,95	1,00	0,90	1,00	1,00	High	-0,05	0,05	80,00	0,94	0,90	0,90
Germany Pro B	6	0,89	0,85	0,85	0,85	0,85	0,85	0,85	1,10	1,15	Medium	-0,10	0,10	80,00	0,94	0,85	0,80
Greek Basket League (A1)	2	1,48	1,70	1,55	1,65	1,65	1,22	1,05	1,05	0,85	High	-0,05	0,05	71,00	1,06	1,10	1,10
Greece Elite League (A2)	5	1,08	1,10	1,05	1,05	1,05	1,00	1,05	1,00	0,90	Estimated	-0,25	0,25	71,00	1,06	1,10	1,00
Hungary NBIA	6	0,89	0,90	0,90	0,90	0,90	0,85	0,85	0,95	1,20	Low	-0,15	0,15	76,00	0,99	0,90	0,85
Iceland Subway League	7	0,69	0,65	0,65	0,65	0,65	0,70	0,85	0,70	1,20	Very Low	-0,20	0,20	80,00	0,94	0,80	0,70
Israel NL(2nd)	5	1,03	1,05	1,00	1,00	1,05	0,95	1,00	1,00	1,00	Estimated	-0,25	0,25	74,00	1,01	1,00	0,95
Italy Lega A2	3	1,26	1,40	1,30	1,30	1,30	1,50	1,05	1,00	0,90	Very Low	-0,20	0,20	75,00	1,00	1,05	1,00
Italy Lega B Nazionale	7	0,64	0,55	0,65	0,55	0,60	0,55	0,80	0,80	1,15	Estimated	-0,25	0,25	75,00	1,00	0,85	0,80
Italy Lega Basket Serie A (LBA)	2	1,52	1,75	1,60	1,70	1,70	1,25	1,08	1,08	0,85	High	-0,05	0,05	74,50	1,01	1,10	1,10
Japan B.League (B1)	4	1,11	1,10	1,10	1,10	1,10	1,10	1,30	1,00	0,95	Very Low	-0,20	0,20	75,00	1,00	0,95	0,90
Kosovo Superliga	7	0,79	0,80	0,80	0,80	0,80	0,60	0,85	0,90	1,20	Very Low	-0,20	0,20	78,00	0,96	0,85	0,90
Lebanon LBL (Div A)	7	0,78	0,70	0,80	0,75	0,70	0,75	0,85	0,90	1,15	Very Low	-0,20	0,20	76,00	0,99	0,85	0,90
Lithuania LKL	3	1,44	1,70	1,45	1,70	1,70	1,50	1,00	1,00	0,80	Very Low	-0,20	0,20	76,00	0,99	1,10	1,05
Lithuania NKL	5	0,96	1,10	1,00	1,05	1,00	0,75	0,90	0,90	1,00	Very Low	-0,20	0,20	76,00	0,99	1,00	0,95
Luxembourg Total	7	0,72	0,70	0,70	0,70	0,70	0,40	1,00	0,85	1,25	Very Low	-0,20	0,20	78,00	0,96	0,70	0,60
Macedonia Superleague	6	0,84	0,85	0,85	0,85	0,85	0,70	0,85	0,90	1,20	Medium	-0,10	0,10	74,00	1,01	0,90	0,85
Mexico CIBACOPA	7	0,66	0,60	0,60	0,60	0,65	0,80	0,80	1,15	Low	-0,15	0,15	80,00	0,94	0,80	0,80
Mexico LNBP (Rest)	5	0,97	1,00	1,00	1,00	1,00	0,90	0,90	1,00	1,05	Low	-0,15	0,15	76,00	0,99	0,95	0,90
Mexico LNBP (Top 4)	4	1,18	1,30	1,25	1,30	1,25	1,10	1,05	1,00	1,00	Estimated	-0,25	0,25	76,00	0,99	1,00	1,00
Montenegro Prva A	6	0,89	0,95	0,90	0,95	0,90	0,70	0,85	0,95	1,10	Medium	-0,10	0,10	72,00	1,04	0,95	0,95
NCAA D1 (High Major)	3	1,23	1,30	1,30	1,30	1,30	1,20	1,10	1,10	0,90	Medium	-0,10	0,10	70,00	1,07	1,10	1,10
NCAA D1 (Mid Major)	4	1,16	1,20	1,20	1,20	1,20	1,20	1,05	1,05	1,00	High	-0,05	0,05	70,00	1,07	1,05	1,05
NCAA D1 (Low Major)	4	1,11	1,15	1,15	1,20	1,20	0,95	1,05	1,05	1,10	High	-0,05	0,05	70,00	1,07	1,00	1,00
NCAA D2 (All)	5	1,00	0,85	1,10	1,05	1,05	0,85	1,05	1,05	1,15	High	-0,05	0,05	72,00	1,04	0,90	0,90
NCAA DIII (Elite)	6	0,82	0,80	0,85	0,80	0,85	0,80	0,85	0,80	1,25	Estimated	-0,25	0,25	78,00	0,96	0,75	0,65
NCAA D3	7	0,76	0,80	0,75	0,80	0,70	0,70	0,80	0,80	1,25	Estimated	-0,25	0,25	78,00	0,96	0,75	0,65
New Zealand NBL	6	0,84	0,85	0,85	0,80	0,80	0,75	0,90	0,95	1,10	Very Low	-0,20	0,20	82,00	0,91	0,85	0,85
Norway BLNO	7	0,59	0,50	0,50	0,50	0,50	0,35	0,85	0,90	1,25	Medium	-0,10	0,10	79,00	0,95	0,65	0,60
Philippines PBA	7	0,70	0,65	0,65	0,60	0,60	0,65	0,85	0,90	1,15	Very Low	-0,20	0,20	79,00	0,95	0,85	0,90
Polish OBL	4	1,15	1,20	1,50	1,20	1,15	1,00	1,00	1,00	0,95	Low	-0,15	0,15	75,50	0,99	1,00	1,00
Poland 1 Liga	6	0,88	0,90	0,80	0,85	0,85	0,95	0,90	0,90	1,10	Estimated	-0,25	0,25	75,00	1,00	0,95	0,95
Portugal LPB (Top 3)	5	0,95	1,00	1,00	0,90	0,90	0,85	0,98	1,00	1,05	Medium	-0,10	0,10	75,00	1,00	0,95	0,95
Portugal LPB (Rest)	6	0,89	0,95	0,90	0,80	0,90	0,90	0,90	0,90	1,15	Very Low	-0,20	0,20	77,00	0,97	0,85	0,80
Puerto Rico BSN	5	1,01	1,00	1,00	1,00	1,00	1,10	0,98	0,98	1,00	Low	-0,15	0,15	78,00	0,96	0,95	0,95
Romania Liga Națională	5	1,02	0,95	0,95	0,95	0,95	1,20	0,95	1,20	1,10	Low	-0,15	0,15	73,00	1,03	0,95	0,95
Serbia KLS	6	0,91	0,90	0,90	0,90	0,90	1,10	0,85	0,80	1,15	Medium	-0,10	0,10	73,00	1,03	0,95	0,90
Slovakia Extraliga	6	0,85	0,85	0,85	0,85	0,85	0,85	0,85	0,85	1,20	Low	-0,15	0,15	75,00	1,00	0,85	0,80
Slovenia SKL	7	0,79	0,80	0,80	0,80	0,80	0,70	0,75	0,85	1,10	Very Low	-0,20	0,20	74,00	1,01	0,90	0,90
Spain Liga ACB	2	1,65	1,85	1,70	1,85	1,85	1,35	1,10	1,10	0,80	High	-0,05	0,05	73,50	1,02	1,12	1,12
Spanish Liga U	7	0,76	0,72	0,72	0,72	0,74	0,80	0,85	0,90	1,15	Estimated	-0,25	0,25	77,00	0,97	0,85	0,80
Spain Primera FEB	3	1,26	1,40	1,40	1,40	1,40	1,00	1,10	1,10	0,90	High	-0,05	0,05	74,00	1,01	1,10	1,10
Spain Segunda FEB	5	1,00	1,00	1,00	1,00	1,00	1,00	1,00	1,00	1,00	High	-0,05	0,05	75,00	1,00	1,00	1,00
Spain Tercera FEB	7	0,78	0,70	0,70	0,75	0,75	0,75	0,85	0,95	1,05	High	-0,05	0,05	76,00	0,99	0,75	0,65
Sweden Basketligan	6	0,87	0,85	0,85	0,85	0,85	0,80	0,95	0,95	1,10	High	-0,05	0,05	76,00	0,99	0,90	0,90
Swiss LNA	5	0,96	0,90	1,00	1,00	1,00	1,25	0,90	0,70	1,10	Very Low	-0,20	0,20	76,00	0,99	0,90	0,85
Taiwan P-League	6	0,82	0,85	0,85	0,80	0,80	0,90	0,90	0,95	1,20	Estimated	-0,25	0,25	80,00	0,94	0,80	0,80
Turkey TBL (Div 2)	5	1,00	1,10	1,00	1,05	1,00	0,85	1,00	1,00	1,05	Very Low	-0,20	0,20	73,00	1,03	1,00	1,00
Ukraine Superleague	4	1,09	1,10	1,10	1,10	1,10	1,10	1,10	1,05	1,00	Low	-0,15	0,15	74,00	1,01	1,00	1,05
Uruguay	7	0,77	0,75	0,70	0,80	0,75	0,85	0,70	0,85	1,15	Very Low	-0,20	0,20	71,50	1,05	0,90	0,85
USA TBL	6	0,83	0,80	0,80	0,80	0,80	0,90	0,85	0,85	1,20	Very Low	-0,20	0,20	82,00	0,91	0,75	0,75
Venezuela SPB	5	0,95	0,95	0,95	0,95	0,95	0,90	0,95	1,00	1,15	High	-0,05	0,05	77,00	0,97	0,90	0,90"""

# ==========================================
# 4. COMPREHENSIVE SITUATIONAL MULTIPLIERS
# ==========================================
SITUATIONAL_MODIFIERS = {
	"Import Boost": {
		"factor": 1.12, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"],
		"desc": "Player is an import in the new league."
	},
	"Rookie Pro": {
		"factor": 0.90, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"],
		"desc": "(Age 22-23, 1st Pro Contract) Penalty for a lack of pro experience."
	},
	"Veteran Leader": {
		"factor": 1.05, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"],
		"desc": "(Age 30+, returning to low tier) Boost for proven pro-level decision-making."
	},
	"NCAA Low As-High Vol": {
		"factor": 1.20, 
		"targets": ["PTS"], # Only affects offensive scoring volume
		"desc": "If player averaged < 2.0 APG and > 10.0 FGA in their final season."
	},
	"Instability Penalty": {
		"factor": 0.90, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"], # Affects overall playing integration / leash
		"desc": "If the player has played for 3 or more teams in the last 3 seasons."
	},
	"Alpha Shadow": {
		"factor": 0.90, 
		"targets": ["PTS", "AST"], # Blocks offensive usage/assists; does not affect effort defense (rebounds/steals)
		"desc": "The new team has a Starter at the same position who averaged >11 FGA or >25 mins."
	},
	"Spacing Liability": {
		"factor": 0.85, 
		"targets": ["PTS", "AST"], # Clogs driving/kick-out passing lanes; no physical effect on defense
		"desc": "Position is PG/SG/SF AND previous season 3P% < 28% (on >1 attempt/game)."
	},
	"Late Arrival": {
		"factor": 0.92, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"], # General playbook/playtime restriction
		"desc": "Player signs after January 1st (Mid-season replacement)."
	},
	"Socialist System": {
		"factor": 0.92, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"], # Flat rotational cap on all minutes/output
		"desc": "Destination Coach plays 9+ man rotation."
	},
	"FTR / Whistle Tax": {
		"factor": 0.95, 
		"targets": ["PTS"], # Exclusively penalizes point production via free-throw volume loss
		"desc": "FTR > 0.40 in Tercera/TBL. Whistles disappear in Segunda FEB."
	},
	"Cantera TOP": {
		"factor": 1.10, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"], 
		"desc": "When leaving Academy for a 'normal' Segunda FEB team, player gets 'Green Light'."
	},
	"Tier Fatigue": {
		"factor": 0.88, 
		"targets": ["PTS", "REB", "OR", "DR", "AST", "STL", "BLK"], # General passive output reduction
		"desc": "Returning to tier for 2nd+ time after playing higher, or 5+ seasons in same tier."
	}
}
TARGET_ROLE_CONFIGS = {
	"A (Franchise Player)": "A (Franchise Player): Consistent high-usage alpha option. (High Usage)",
	"B (Core Starter)": "B (Core Starter): Consistent offensive option, but plays within the system. (Medium-High Usage)",
	"C (Rotation Player)": "C (Rotation Player): Secondary option off the bench. (Medium Usage)",
	"D (Glue / Specialist)": "D (Glue / Specialist): Defensive anchor, screener, spot-up shooter. Does not force shots. (Low Usage)"
}
# ==========================================
# 5. NCAA D1 CONFERENCE TO SCOUTING TIER MAPPING
# ==========================================
NCAA_CONFERENCE_MAPPING = {
	"ACC (Atlantic Coast Conference)": "NCAA D1 (High Major)",
	"Atlantic 10": "NCAA D1 (High Major)",
	"Big 12": "NCAA D1 (High Major)",
	"Big East": "NCAA D1 (High Major)",
	"Big Ten": "NCAA D1 (High Major)",
	"Mountain West": "NCAA D1 (High Major)",
	"Pac-12": "NCAA D1 (High Major)",
	"SEC (Southeastern Conference)": "NCAA D1 (High Major)",
	
	"American": "NCAA D1 (Mid Major)",
	"Big Sky Conference": "NCAA D1 (Mid Major)",
	"Big West Conference": "NCAA D1 (Mid Major)",
	"Coastal Athletic Association (CAA)": "NCAA D1 (Mid Major)",
	"Conference USA (C-USA)": "NCAA D1 (Mid Major)",
	"Ivy League": "NCAA D1 (Mid Major)",
	"Mid-American Conference (MAC)": "NCAA D1 (Mid Major)",
	"Missouri Valley Conference (MVC)": "NCAA D1 (Mid Major)",
	"West Coast Conference (WCC)": "NCAA D1 (Mid Major)",
	"Western Athletic Conference (WAC)": "NCAA D1 (Mid Major)",
	
	"America East Conference": "NCAA D1 (Low Major)",
	"ASUN (Atlantic Sun) Conference": "NCAA D1 (Low Major)",
	"Big South Conference": "NCAA D1 (Low Major)",
	"Colonial Athletic Association (CAA)": "NCAA D1 (Low Major)",
	"Horizon League": "NCAA D1 (Low Major)",
	"Metro Atlantic": "NCAA D1 (Low Major)",
	"Mid-Eastern Athletic Conference (MEAC)": "NCAA D1 (Low Major)",
	"Northeast Conference (NEC)": "NCAA D1 (Low Major)",
	"Ohio Valley Conference (OVC)": "NCAA D1 (Low Major)",
	"Patriot League": "NCAA D1 (Low Major)",
	"Southern": "NCAA D1 (Low Major)",
	"Southland": "NCAA D1 (Low Major)",
	"Southwestern Athletic Conference (SWAC)": "NCAA D1 (Low Major)",
	"Summit League": "NCAA D1 (Low Major)",
	"Sun Belt Conference": "NCAA D1 (Low Major)"
}

@st.cache_data
def load_league_data(raw_data_str=RAW_LEAGUE_DATA):
	cleaned_data = raw_data_str.replace(",", ".")
	df = pd.read_csv(io.StringIO(cleaned_data), sep="\t")
	numeric_cols = [
		"Rate", "Bigs (C/PF)", "Shooters (SG/SF)", "Slashers / D(Non-Shooters)", 
		"Slashers/PGs", "Rebounds", "2P% Modifier", "3P% Modifier", "TO Modifier", 
		"T High", "T Low", "Pace", "Pace Factor", "STL Modifier", "BLK Modifier"
	]
	for col in numeric_cols:
		df[col] = pd.to_numeric(df[col], errors='coerce')
	return df

# ==========================================
# 6. LOAD LEAGUE DATA
# ==========================================
leagues_df = load_league_data(RAW_LEAGUE_DATA)

# ==========================================
# 7. HELPER PARSING FUNCTIONS AND CALLBACKS
# ==========================================
# ==========================================
# REALGM LOADER (Ensure this is in the top half of your script)
# ==========================================
def load_scraped_row_into_state(df_row, player_name, height_cm, yob, team_name="Unknown Team"):
    row_dict = {str(k).upper(): v for k, v in df_row.items()}
    
    def get_val(keys_list, default=0.0):
        for k in keys_list:
            if k in row_dict:
                try: 
                    # Clean commas from RealGM numbers (e.g. "1,338" -> 1338.0)
                    return float(str(row_dict[k]).replace(",","").strip())
                except: pass
        return default

    # 1. Map the current selected row stats
    mapped_stats = {
        "MIN": get_val(["MIN"]), "PTS": get_val(["PTS"]), "FGM": get_val(["FGM", "FG"]), "FGA": get_val(["FGA"]),
        "FG%": get_val(["FG%"]), "3PM": get_val(["3PM", "3FG", "3P"]), "3PA": get_val(["3PA", "3FGA"]),
        "3P%": get_val(["3P%", "3FG%"]), "FTM": get_val(["FTM", "FT"]), "FTA": get_val(["FTA"]),
        "FT%": get_val(["FT%"]), "OFF": get_val(["OFF", "ORB"]), "DEF": get_val(["DEF", "DRB"]),
        "TRB": get_val(["TRB", "REB"]), "AST": get_val(["AST"]), "STL": get_val(["STL"]),
        "BLK": get_val(["BLK"]), "TOV": get_val(["TOV", "TO"]), "PF": get_val(["PF"])
    }

    # 2. COLLECT HISTORY (Specifically looking for 'Totals' tables for weighted blend)
    rgm_history = []
    rgm_profile = st.session_state.get("parsed_player_profile")
    if rgm_profile and "tables" in rgm_profile:
        for table in rgm_profile["tables"]:
            # Only pull from Totals tables to ensure high volume for career blend
            if "TOTALS" in table["name"].upper():
                rgm_history.extend(table["df"].to_dict('records'))

    # 3. BLEND SHOOTING WITH HISTORY
    if rgm_history:
        weighted = calculate_weighted_shooting(rgm_history, mapped_stats, source="RealGM")
        mapped_stats["FG%"] = round(weighted["weighted_fg%"], 3)
        mapped_stats["3P%"] = round(weighted["weighted_3p%"], 3)
        st.session_state.shooting_debug = weighted["debug"]

    # 4. Update Persistent State
    st.session_state.raw_stats = mapped_stats
    st.session_state.p_player_name = player_name
    st.session_state.p_orig_team = str(team_name)
    if height_cm: st.session_state.p_height = height_cm
    if yob: st.session_state.p_yob = yob
        
    gp_val = get_val(["GP"])
    if gp_val > 0: st.session_state.p_gp = int(gp_val)

    # 5. RUN AUTO-SCOUT (Using the history we collected in step 2)
    st.session_state.p_selected_modifiers = auto_detect_situational_modifiers(
        mapped_stats, 
        yob if yob else 2005, 
        height_cm if height_cm else 185, 
        int(gp_val if gp_val else 1),
        st.session_state.p_orig_league_name, 
        st.session_state.p_dest_league_name, 
        st.session_state.p_position,
        history = rgm_history 
    )

    # 6. SYNC AND CLEAR UI
    st.session_state.t_selected_modifiers = st.session_state.p_selected_modifiers
    
    t_keys_to_clear = [
        "t_player_name", "t_orig_team", "t_yob", "t_height", 
        "t_gp", "t_position", "t_selected_modifiers"
    ]
    for k in t_keys_to_clear:
        if k in st.session_state:
            del st.session_state[k]
def add_to_shortlist_callback(
	name, orig_team, orig_league, dest_league, position, target_role, calculated_age, height_cm, gp, 
	raw_stats, proj, total_risk_score, total_context_multiplier, active_modifiers, variability,
	origin_role_label, target_role_label, conf_percent, conf_factor_str, risk_badge, dest_cs, badges_str, stars_str
):
	# Avoid adding duplicate entries for the same player going to the same league
	exists = any(item["name"] == name and item["dest_league"] == dest_league for item in st.session_state.shortlist)
	if not exists:
		# Helper for dynamic variance boundaries
		def apply_var(val, var, direction=1):
			return round(val * (1 + (var * direction)), 1)
			
		# Explicitly copy and reassign to force Streamlit state-detection persistence
		temp_shortlist = list(st.session_state.shortlist)
		temp_shortlist.append({
			# 1. Base Descriptive Stats
			"name": name,
			"orig_team": orig_team,
			"orig_league": orig_league,
			"dest_league": dest_league,
			"position": position,
			"target_role": target_role,
			"calculated_age": calculated_age,
			"height_cm": height_cm,
			"gp": gp,
			
			# 2. Raw Statistics (Baseline Inputs)
			"raw_min": raw_stats.get("MIN", 0.0),
			"raw_pts": raw_stats.get("PTS", 0.0),
			"raw_fgm": raw_stats.get("FGM", 0.0),
			"raw_fga": raw_stats.get("FGA", 0.0),
			"raw_fg_pct": f"{raw_stats.get('FG%', 0.0)*100:.1f}%",
			"raw_3pm": raw_stats.get("3PM", 0.0),
			"raw_3pa": raw_stats.get("3PA", 0.0),
			"raw_3p_pct": f"{raw_stats.get('3P%', 0.0)*100:.1f}%",
			"raw_ftm": raw_stats.get("FTM", 0.0),
			"raw_fta": raw_stats.get("FTA", 0.0),
			"raw_ft_pct": f"{raw_stats.get('FT%', 0.0)*100:.1f}%",
			"raw_off": raw_stats.get("OFF", 0.0),
			"raw_def": raw_stats.get("DEF", 0.0),
			"raw_trb": raw_stats.get("TRB", 0.0),
			"raw_ast": raw_stats.get("AST", 0.0),
			"raw_stl": raw_stats.get("STL", 0.0),
			"raw_blk": raw_stats.get("BLK", 0.0),
			"raw_tov": raw_stats.get("TOV", 0.0),
			"raw_pf": raw_stats.get("PF", 0.0),
			
			# 3. Projected Statistics (Outputs)
			"pts": proj["PTS"],
			"reb": proj["REB"],
			"proj_or": proj["OR"],
			"proj_dr": proj["DR"],
			"proj_ast": proj["AS"],
			"proj_tov": proj["TO"],
			"proj_stl": proj["STL"],
			"proj_blk": proj["BLK"],
			"proj_2p_pct": f"{proj['2P%']*100:.1f}%",
			"proj_3p_pct": f"{proj['3P%']*100:.1f}%",
			"proj_ft_pct": f"{proj['FT%']*100:.1f}%",
			"val": proj["VAL"],
			
			# 4. Range Limits (Floors & Ceilings)
			"floor_pts": apply_var(proj["PTS"], variability, -1),
			"ceil_pts": apply_var(proj["PTS"], variability, 1),
			"floor_reb": apply_var(proj["REB"], variability, -1),
			"ceil_reb": apply_var(proj["REB"], variability, 1),
			"floor_ast": apply_var(proj["AS"], variability, -1),
			"ceil_ast": apply_var(proj["AS"], variability, 1),
			"floor_val": apply_var(proj["VAL"], variability, -1),
			"ceil_val": apply_var(proj["VAL"], variability, 1),
			
			# 5. Risk & Context Metrics (Qualitative & Numerical Values)
			"risk_score": round(total_risk_score, 1),
			"context_multiplier": round(total_context_multiplier, 2),
			"active_modifiers": ", ".join(active_modifiers) if active_modifiers else "None",
			"origin_role_label": origin_role_label,
			"target_role_label": target_role_label,
			"conf_percent": f"{conf_percent}%",
			"conf_factor_str": conf_factor_str,
			"risk_badge": risk_badge,
			"dest_cs": dest_cs,
			"badges_str": badges_str,
			"stars_str": stars_str
		})
		st.session_state.shortlist = temp_shortlist

def clear_shortlist_callback():
	st.session_state.shortlist = []

def auto_detect_situational_modifiers(stats, yob, height, gp, orig_league_name, dest_league_name, position, history=None):
	suggested = []
	age = 2026 - yob
	
	# Get League Data for Tiers
	orig_row = leagues_df[leagues_df["League"] == orig_league_name].iloc[0]
	dest_row = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]
	orig_tier = float(orig_row["Tier"])
	dest_tier = float(dest_row["Tier"])
	is_ncaa_origin = "NCAA" in orig_league_name

	# 1. Rookie Pro (Age 21-25, coming from NCAA)
	if age in [21, 22, 23, 24, 25] and is_ncaa_origin and "NCAA" not in dest_league_name:
		suggested.append("Rookie Pro")
		
	# 2. Veteran Leader (Age 30+ descending to Tier 5 or lower)
	if age >= 30 and dest_tier >= 5.0:
		suggested.append("Veteran Leader")
		
	# 3. NCAA Low As-High Vol (< 2.0 APG and > 10.0 FGA)
	ast = stats.get("AST", 0.0)
	fga = stats.get("FGA", 0.0)
	if is_ncaa_origin and ast < 2.0 and fga > 10.0:
		suggested.append("NCAA Low As-High Vol")

	# 4. Spacing Liability (PG/SG/SF, < 28% 3P, > 1.0 3PA)
	threep_pct = stats.get("3P%", 0.0)
	threep_att = stats.get("3PA", 0.0)
	if position != "Bigs (C/PF)" and threep_pct < 0.28 and threep_att > 1.0:
		suggested.append("Spacing Liability")

	# 5. FTR / Whistle Tax (FTA/FGA > 0.42, Tier 6/7 to Tier 5)
	ftm = stats.get("FTM", 0.0)
	fga = stats.get("FGA", 0.0)
	raw_ftr = ftm / fga if fga > 0 else 0.0
	if raw_ftr > 0.42 and orig_tier >= 6.0 and dest_tier <= 5.0:
		suggested.append("FTR / Whistle Tax")

	# --- HISTORY BASED CHECKS ---
	if history and len(history) >= 2:
		# 6. Instability Penalty (3+ teams in last 3 active rows)
		valid_history_teams = []
		for row in history:
			team = str(row.get('equipo', row.get('TEAM', row.get('SCHOOL', '')))).strip()
			# Ignore RealGM aggregate summary rows
			if team and team.lower() not in ["all teams", "two teams", "three teams", "four teams", "total", "overall", "career", ""]:
				valid_history_teams.append(team)
				
		# RealGM is oldest-first (newest at end). FEB is newest-first (newest at start).
		is_feb = any('equipo' in r for r in history[:1])
		recent_teams = valid_history_teams[:3] if is_feb else valid_history_teams[-3:]
			
		unique_teams = len(set(recent_teams))
		if unique_teams >= 3:
			suggested.append("Instability Penalty")

		# 7. Tier Fatigue (5+ seasons in same tier OR returning after playing higher)
		chrono_history = history[::-1] if is_feb else history
		historical_tiers = []
		
		for h_row in chrono_history:
			h_league = h_row.get('league', h_row.get('LEAGUE', h_row.get('CONFERENCE', '')))
			
			# FEB Fallback: Match dynamically parsed leagues
			if not h_league and 'stats' in h_row:
				if dest_league_name in ["Spain Segunda FEB", "Spain Primera FEB", "Spain Tercera FEB"]:
					h_league = dest_league_name
					
			if h_league in leagues_df['League'].values:
				h_tier = float(leagues_df[leagues_df['League'] == h_league]['Tier'].iloc[0])
				historical_tiers.append(h_tier)
				
		# Condition A: Stagnation (5+ seasons in the target tier)
		tier_count = historical_tiers.count(dest_tier)
		
		# Condition B: Return (Returning to this tier after playing higher)
		returned_after_higher = False
		if dest_tier in historical_tiers:
			first_occurrence_idx = historical_tiers.index(dest_tier)
			# Find if they played in any stronger tier (lower number) chronologically AFTER their first stint here
			returned_after_higher = any(t < dest_tier for t in historical_tiers[first_occurrence_idx + 1:])
			
		if tier_count >= 5 or returned_after_higher:
			suggested.append("Tier Fatigue")
		
	return suggested

def apply_suggestions_callback(suggestions_to_add):
	# Get current active modifiers from the source of truth
	current_mods = list(st.session_state.get("p_selected_modifiers", []))
	updated_mods = list(set(current_mods + suggestions_to_add))
	# Sync back to both slots
	st.session_state.p_selected_modifiers = updated_mods
	st.session_state.t_selected_modifiers = updated_mods

def parse_realgm_row(pasted_text):
	cleaned = pasted_text.replace(",", ".").strip()
	parts = cleaned.split()
	
	nums = []
	for p in parts:
		try:
			nums.append(float(p))
		except ValueError:
			pass
			
	if len(nums) < 21:
		return None, None, f"Parsing failed. Found only {len(nums)} numbers. Ensure you copied a complete RealGM row."
		
	try:
		stats = {
			"MIN": nums[2], "PTS": nums[3], "FGM": nums[4], "FGA": nums[5], "FG%": nums[6],
			"3PM": nums[7], "3PA": nums[8], "3P%": nums[9], "FTM": nums[10], "FTA": nums[11],
			"FT%": nums[12], "OFF": nums[13], "DEF": nums[14], "TRB": nums[15],
			"AST": nums[16], "STL": nums[17], "BLK": nums[18], "TOV": nums[19],
			"PF": nums[20]
		}
		gp_val = int(nums[0])
		return stats, gp_val, None
	except Exception as e:
		return None, None, f"Mapping failed: {str(e)}"

def search_realgm_players(query):
	query = query.strip()
	
	# Direct URL Bypass: If the user inputs a direct RealGM player profile link, load it instantly
	if query.startswith("http") and "realgm.com/player/" in query and "/Summary/" in query:
		return {"direct_match": True, "url": query, "html": None}, None
		
	# De-duplicate repeating tokens inside the search query to bypass RealGM's token-repeating server bug
	tokens = query.split()
	seen = set()
	unique_tokens = []
	for t in tokens:
		if t.lower() not in seen:
			seen.add(t.lower())
			unique_tokens.append(t)
	clean_query = " ".join(unique_tokens)
	
	encoded_query = urllib.parse.quote(clean_query)
	url = f"https://basketball.realgm.com/search?q={encoded_query}"
	
	html_text, final_url, err = fetch_html_content(url)
	if err:
		return None, f"Failed to connect to RealGM (Error: {err})"
		
	if "/player/" in final_url and "/Summary/" in final_url:
		return {"direct_match": True, "url": final_url, "html": html_text}, None
		
	soup = BeautifulSoup(html_text, 'html.parser')
	results = []
	
	for link in soup.find_all('a', href=re.compile(r'/player/[^/]+/Summary/\d+')):
		href = link.get('href')
		full_url = f"https://basketball.realgm.com{href}"
		name = link.get_text().strip()
		
		parent_row = link.find_parent('tr')
		details = ""
		if parent_row:
			cells = [td.get_text().strip() for td in parent_row.find_all('td')]
			if len(cells) > 1:
				details = " | ".join([c for c in cells if c and c != name])
		
		results.append({
			"name": name,
			"url": full_url,
			"details": details if details else "Player Summary Profile"
		})
		
	if not results:
		return None, "No matching players found on RealGM. Check spelling or enter stats manually."
		
	seen_urls = set()
	unique_results = []
	for r in results:
		if r["url"] not in seen_urls:
			seen_urls.add(r["url"])
			unique_results.append(r)
			
	return {"direct_match": False, "results": unique_results}, None

def parse_player_summary(url, html_text=None):
	if not html_text:
		html_text, _, err = fetch_html_content(url)
		if err:
			return None, f"Failed to load player profile (Error: {err})"
			
	soup = BeautifulSoup(html_text, 'html.parser')
	
	height_cm = None
	yob = None
	
	profile_div = soup.find('div', class_='profile-box') or soup.find('p', class_='player-bio') or soup
	profile_text = profile_div.get_text()
	
	height_match = re.search(r'\((\d{3})cm\)', profile_text)
	if height_match:
		height_cm = int(height_match.group(1))
		
	born_match = re.search(r'Born:\s+[A-Za-z]{3}\s+\d{1,2},\s+(\d{4})', profile_text)
	if born_match:
		yob = int(born_match.group(1))
	else:
		born_fallback = re.search(r'Born:.*?(\d{4})', profile_text)
		if born_fallback:
			yob = int(born_fallback.group(1))
			
	tables = soup.find_all('table')
	parsed_tables = []
	
	for idx, table in enumerate(tables):
		heading_text = f"Table {idx + 1}"
		prev_node = table.find_previous()
		while prev_node:
			if prev_node.name in ['h2', 'h3', 'h4']:
				heading_text = prev_node.get_text().strip()
				break
			if prev_node.name == 'body':
				break
			prev_node = prev_node.find_previous()
			
		try:
			df_list = pd.read_html(io.StringIO(str(table)))
			if not df_list:
				continue
			df = df_list[0]
			df.columns = [str(c).strip().upper() for c in df.columns]
			
			required_cols = {"MIN", "PTS", "FGA", "FTA"}
			if not required_cols.issubset(set(df.columns)):
				continue
				
			if "SEASON" in df.columns:
				df = df[~df["SEASON"].astype(str).str.contains("Career|Total|Overall|All-Star", case=False, na=False)]
				
			parsed_tables.append({
				"name": heading_text,
				"df": df
			})
		except Exception:
			continue
			
	if not parsed_tables:
		return None, "No valid averages tables found on this player profile."
		
	name_counts = {}
	for t in parsed_tables:
		name = t["name"]
		name_counts[name] = name_counts.get(name, 0) + 1
		
	seen_counts = {}
	for t in parsed_tables:
		name = t["name"]
		if name_counts[name] > 1:
			seen_counts[name] = seen_counts.get(name, 0) + 1
			if seen_counts[name] == 1:
				t["name"] = f"{name} - Averages"
			elif seen_counts[name] == 2:
				t["name"] = f"{name} - Totals"
			else:
				t["name"] = f"{name} - Table {seen_counts[name]}"
				
	return {
		"height_cm": height_cm,
		"yob": yob,
		"tables": parsed_tables
	}, None

def bs4_to_dataframe(table_element):
	tr_elements = table_element.find_all('tr')
	parsed_rows = []
	
	for tr in tr_elements:
		cells = tr.find_all(['td', 'th'])
		row_data = []
		for cell in cells:
			val = cell.get_text().replace('\xa0', ' ').strip()
			row_data.append(val)
		if row_data:
			parsed_rows.append(row_data)
			
	if not parsed_rows:
		return pd.DataFrame()
		
	max_cols = max(len(r) for r in parsed_rows)
	padded_rows = [r + [''] * (max_cols - len(r)) for r in parsed_rows]
	
	return pd.DataFrame(padded_rows)

# Added 'row_label' and 'history' to the arguments
def load_feb_row_into_state(df_row, player_name, height_cm, yob, team_name, gp, row_label, history):
    # 1. Normalize row data
    row = {str(k).upper(): str(v).replace(",", ".").strip() for k, v in df_row.items()}
    gp_val = float(gp) if float(gp) > 0 else 1.0

    def split_stat(val_str):
        if "-" in str(val_str):
            parts = str(val_str).split("-")
            return float(parts[0]), float(parts[1])
        return 0.0, 0.0

    def parse_min(val_str):
        if ":" in str(val_str):
            p = str(val_str).split(":")
            return float(p[0]) + (float(p[1])/60.0)
        try: return float(val_str)
        except: return 0.0

    m2, a2 = split_stat(row.get("T2", "0-0"))
    m3, a3 = split_stat(row.get("T3", "0-0"))
    mft, aft = split_stat(row.get("TL", "0-0"))
    
    mapped_stats = {
        "MIN": round(parse_min(row.get("MIN", "0")) / gp_val, 1),
        "PTS": round(float(row.get("PT", 0)) / gp_val, 1),
        "FGM": round((m2+m3) / gp_val, 3), 
        "FGA": round((a2+a3) / gp_val, 3), 
        "FG%": round((m2+m3)/(a2+a3), 3) if (a2+a3) > 0 else 0.0,
        "3PM": round(m3 / gp_val, 3), 
        "3PA": round(a3 / gp_val, 3), 
        "3P%": round(m3 / a3, 3) if a3 > 0 else 0.0,
        "FTM": round(mft / gp_val, 3), 
        "FTA": round(aft / gp_val, 3), 
        "FT%": round(mft / aft, 3) if aft > 0 else 0.0,
        "OFF": round(float(row.get("RO", 0)) / gp_val, 1),
        "DEF": round(float(row.get("RD", 0)) / gp_val, 1),
        "TRB": round(float(row.get("RT", 0)) / gp_val, 1),
        "AST": round(float(row.get("AS", 0)) / gp_val, 1),
        "STL": round(float(row.get("BR", 0)) / gp_val, 1),
        "BLK": round(float(row.get("TF", 0)) / gp_val, 1),
        "TOV": round(float(row.get("BP", 0)) / gp_val, 1),
        "PF": round(float(row.get("FC", 0)) / gp_val, 1)
    }

    # 2. BLEND WITH FEB HISTORY
    if history:
        weighted = calculate_weighted_shooting(history, mapped_stats, source="FEB", skip_label=row_label)
        mapped_stats["FG%"] = round(weighted["weighted_fg%"], 3)
        mapped_stats["3P%"] = round(weighted["weighted_3p%"], 3)
        st.session_state.shooting_debug = weighted["debug"]

    # 3. Update Persistent State
    st.session_state.raw_stats = mapped_stats
    st.session_state.p_player_name = player_name
    st.session_state.p_orig_team = team_name
    st.session_state.p_gp = int(gp_val)
    st.session_state.p_height = int(height_cm) if height_cm else 0
    st.session_state.p_yob = int(yob)

    # 4. Auto-detect Situational Modifiers
    st.session_state.p_selected_modifiers = auto_detect_situational_modifiers(
        mapped_stats, int(yob), int(height_cm), int(gp_val), 
        st.session_state.p_orig_league_name, st.session_state.p_dest_league_name, 
        st.session_state.p_position, history=history
    )
    st.session_state.t_selected_modifiers = st.session_state.p_selected_modifiers

    # 5. Force UI Refresh
    t_keys_to_clear = ["t_player_name", "t_orig_team", "t_yob", "t_height", "t_gp", "t_selected_modifiers"]
    for k in t_keys_to_clear:
        if k in st.session_state:
            del st.session_state[k]
def parse_feb_player_profile(url, html_text=None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            html_text = response.read().decode('utf-8', errors='ignore')
            final_url = response.geturl()
    except: return None, "Connection failed."

    soup = BeautifulSoup(html_text, 'html.parser')
    # --- SCRAPE PLAYER NAME ---
    player_name = "FEB Player"
    name_node = soup.find('div', class_='nombre-jugador') or soup.find('div', class_='nombre')
    if name_node:
        player_name = name_node.get_text().strip().title()
    # ------------------------------------
    # CLICK TRAYECTORIA (The Postback)
    career_btn = soup.find('input', id=re.compile(r'estadisticasCarreraButton', re.IGNORECASE))
    if career_btn:
        post_data = {
            "__VIEWSTATE": soup.find('input', id='__VIEWSTATE')['value'] if soup.find('input', id='__VIEWSTATE') else '',
            "__VIEWSTATEGENERATOR": soup.find('input', id='__VIEWSTATEGENERATOR')['value'] if soup.find('input', id='__VIEWSTATEGENERATOR') else '',
            "__EVENTVALIDATION": soup.find('input', id='__EVENTVALIDATION')['value'] if soup.find('input', id='__EVENTVALIDATION') else '',
            career_btn['name']: career_btn['value']
        }
        for h in soup.find_all('input', type='hidden'):
            if h.get('name') not in post_data: post_data[h.get('name')] = h.get('value', '')
        try:
            encoded_data = urllib.parse.urlencode(post_data).encode('utf-8')
            req_post = urllib.request.Request(final_url, data=encoded_data, headers=headers, method="POST")
            with urllib.request.urlopen(req_post, timeout=12) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')
                soup = BeautifulSoup(html_text, 'html.parser')
        except: pass

    # --- DYNAMIC CATEGORY TO STANDARDIZED LEAGUE MAPPING ---
    FEB_CATEGORY_MAP = {
        "SEGUNDA FEB": "Spain Segunda FEB",
        "LEB PLATA": "Spain Segunda FEB",
        "PRIMERA FEB": "Spain Primera FEB",
        "LEB ORO": "Spain Primera FEB",
        "TERCERA FEB": "Spain Tercera FEB",
        "LIGA EBA": "Spain Tercera FEB",
        "ACB": "Spain Liga ACB",
        "LIGA ENDESA": "Spain Liga ACB"
    }

    temp_category_map = {}
    trayectoria_marker = soup.find(string=re.compile(r'Trayectoria Nacional', re.IGNORECASE))
    if trayectoria_marker:
        trayectoria_table = trayectoria_marker.find_next('table')
        if trayectoria_table:
            t_rows = trayectoria_table.find_all('tr')
            for tr in t_rows:
                t_cells = tr.find_all(['td', 'th'])
                if len(t_cells) >= 3:
                    temp_val = t_cells[0].get_text().strip()              # e.g., "24/25"
                    categ_val = t_cells[1].get_text().strip().upper()      # e.g., "TERCERA FEB"
                    club_val = t_cells[2].get_text().strip().upper()       # e.g., "MATARÓ PARC BOET [MATARÓ PARC BOET]"
                    
                    if temp_val and categ_val and club_val:
                        # Clean out bracketed text: "MATARÓ PARC BOET [MATARÓ PARC BOET]" -> "MATARÓ PARC BOET"
                        base_club = re.sub(r'\s*\[.*?\]', '', club_val).strip()
                        temp_category_map[(temp_val, base_club)] = FEB_CATEGORY_MAP.get(categ_val, categ_val)

    def find_league_for_row(temp, team):
        # Normalize strings by stripping all spaces, periods, and hyphens for comparison
        def clean_str(s):
            return re.sub(r'[^A-Z0-9]', '', s.upper())
            
        normalized_team = clean_str(team)
        if not normalized_team:
            return None
            
        # 1. Match by exact season + normalized team string containment
        for (t_temp, t_club), league in temp_category_map.items():
            if t_temp == temp:
                cleaned_club = clean_str(t_club)
                if normalized_team in cleaned_club or cleaned_club in normalized_team:
                    return league
        # 2. Fallback: match by team string containment regardless of season
        for (t_temp, t_club), league in temp_category_map.items():
            cleaned_club = clean_str(t_club)
            if normalized_team in cleaned_club or cleaned_club in normalized_team:
                return league
        return None

    # FIND THE "TOTALES" TABLE
    table = None
    totals_marker = soup.find(string=re.compile(r'Totales'))
    if totals_marker:
        table = totals_marker.find_next('table')
    
    if not table: return None, "Totals table not found."

    rows = table.find_all('tr')
    parsed_rows = []
    curr_temp, curr_team = "Unknown", "Unknown Team"
    cols = ["FASE", "PART", "MIN", "PT", "T2", "T3", "TC", "TL", "RO", "RD", "RT", "AS", "BR", "BP", "TF", "TC_C", "MT", "FC", "FR", "VA"]

    for tr in rows:
        text = tr.get_text(separator=" ").strip()
        cells = tr.find_all(['td', 'th'])
        
        if "Temp:" in text:
            t_m = re.search(r'Temp:\s*(\d{2}/\d{2})', text)
            e_m = re.search(r'Equipo:\s*(.+)', text)
            if t_m: 
                curr_temp = t_m.group(1)
            if e_m: 
                raw_team = e_m.group(1).strip()
                # Clean up trailing hyphens/dashes from the ends of the string (e.g., "HOMS U.E.MATARÓ --" -> "HOMS U.E.MATARÓ")
                curr_team = re.sub(r'\s*[-—–]+$', '', raw_team).strip().upper()
            continue
        if len(cells) >= 18:
            fase = cells[0].get_text().strip()
            if fase.upper() in ["FASE", ""] or fase.isdigit(): continue
            
            row_data = [c.get_text().strip() for c in cells]
            row_dict = dict(zip(cols, row_data))
            
            # Match the current row with the Trayectoria Nacional list
            parsed_league_name = find_league_for_row(curr_temp, curr_team)
            
            parsed_rows.append({
                "label": f"{curr_team} {curr_temp} {fase}",
                "equipo": curr_team,
                "league": parsed_league_name,  # Mapped domestic Spanish league name
                "stats": row_dict
            })

    # --- BIO SCRAPING (Height and YOB) ---
    height_cm = 0 # Default
    yob = 2005      # Default
    
    page_text = soup.get_text()
    
    # Original regex is correct
    h_match = re.search(r'(?:Altura|Alt)\s*[:.]?\s*(\d{3})', page_text, re.IGNORECASE)
    if h_match:
        height_cm = int(h_match.group(1))
        
    b_match = re.search(r'(?:Nacimiento|Fecha)\s*[:.]?\s*\d{2}/\d{2}/(\d{4})', page_text, re.IGNORECASE)
    if b_match:
        yob = int(b_match.group(1))
    # ------------------------------------

    return {
        "player_name": player_name, 
        "height_cm": height_cm, 
        "yob": yob, 
        "rows": parsed_rows
    }, None


# ==========================================
# 8. EXACT EXCEL-ALIGNED STATS CONVERSION ENGINE
# ==========================================
dest_row = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]
league_default_var = abs(float(dest_row["T Low"])) if not pd.isna(dest_row["T Low"]) else 0.20

# If the user hasn't manually adjusted the slider, force it to the league default
if "t_variability" not in st.session_state:
	st.session_state.p_variability = league_default_var
def run_projection_engine(
	raw_stats, origin_league, dest_league, target_role_mod, position, 
	yob, height, age_mod, height_mod_reb, height_mod_2p,
	active_modifiers, target_min, target_role, baseline_variability,
	orig_team_quality="Mid-Table (Default)"
):
	# --- STEP 1: BASELINE DATA & SIZING LOGIC ---
	minutes = raw_stats["MIN"] if raw_stats["MIN"] > 0 else 1.0
	player_age = 2026 - yob
	h_cm = 190.0 if height == 0 else (height * 100.0 if height < 3.0 else height)
	
	# --- DYNAMIC AGE-DEVELOPMENT GROWTH & DECAY CURVE ---
	if player_age <= 21:
		age_mod = 1.04     # Prospect Growth (+4%)
	elif player_age <= 24:
		age_mod = 1.02     # Young Development (+2%)
	elif player_age <= 29:
		age_mod = 1.00     # Peak Plateau (Neutral)
	elif player_age <= 32:
		age_mod = 0.98     # Early Veteran Decline (-2%)
	elif player_age <= 35:
		age_mod = 0.95     # Late Veteran Decline (-5%)
	else:
		age_mod = 0.92     # Veteran athletic decay (-8%)
	
	is_defensive_glue = (raw_stats.get("FGA", 0) / minutes) < 0.35
	origin_tier = float(origin_league["Tier"])
	dest_tier = float(dest_league["Tier"])
	is_moving_up = (dest_tier <= 5.0) and (origin_tier >= 6.0)

	rebound_height_modifier = 1.0
	derived_height_mod_2p = 1.0

	# EXACT EXCEL LOGIC: Apply size penalties if moving up a tier
	if is_moving_up:
		if position == "Bigs (C/PF)" and h_cm < 201:
			rebound_height_modifier = 0.90
			derived_height_mod_2p = 0.90
		elif position in ["Slashers/PGs", "Slashers / D(Non-Shooters)"] and h_cm < 185:
			rebound_height_modifier = 0.85
			derived_height_mod_2p = 0.85

	pts10 = (raw_stats["PTS"] / minutes) * 10
	reb10 = (raw_stats["TRB"] / minutes) * 10
	ast10 = (raw_stats["AST"] / minutes) * 10
	or10 = (raw_stats["OFF"] / minutes) * 10
	dr10 = (raw_stats["DEF"] / minutes) * 10
	to10 = (raw_stats["TOV"] / minutes) * 10
	stl10 = (raw_stats["STL"] / minutes) * 10
	blk10 = (raw_stats["BLK"] / minutes) * 10

	# --- STEP 2: POSITION & PACE (The "Jump" Modifiers) ---
	pos_pts = origin_league[position] / dest_league[position]
	league_reb_ratio = origin_league["Rebounds"] / dest_league["Rebounds"]
	
	# Position-based Rebound Adjustment
	pos_reb_adjustment = 1.0
	if position == "Bigs (C/PF)":
		pos_reb_adjustment = 1.15 
	elif "PG" in position or "Slasher" in position:
		pos_reb_adjustment = 0.90 
		
	# MULTIPLYING THE HEIGHT PENALTY INTO REBOUNDS
	pos_reb = league_reb_ratio * pos_reb_adjustment * rebound_height_modifier
	pace = 0.99 if (origin_league["Tier"] == 7 and dest_league["Tier"] == 5) else (dest_league["Pace"] / origin_league["Pace"])

	pts_base = pts10 * pos_pts * pace
	reb_base = reb10 * pos_reb * pace
	ast_base = ast10 * pos_pts * pace 
	or_base = or10 * pos_reb * pace   
	dr_base = dr10 * pos_reb * pace   

	# --- STEP 3: RTSMOOTH ---
	if target_role == "C (Rotation Player)":
		pts_smoothed = pts_base if pts_base <= 3.0 else 3.0 + (pts_base - 3.0) / 2
		if reb_base > 2.25: reb_smoothed = 2.25 + (reb_base - 2.25) / 4
		elif reb_base > 2.0: reb_smoothed = 2.0 + (reb_base - 2.0) / 2
		else: reb_smoothed = reb_base
	else:
		pts_smoothed = pts_base if pts_base <= 2.5 else 2.5 + (pts_base - 2.5) / 2
		reb_smoothed = reb_base if reb_base <= 2.5 else 2.5 + (reb_base - 2.5) / 2

	reb_ratio = reb_smoothed / reb_base if reb_base > 0 else 1.0
	or_smoothed = or_base * reb_ratio
	dr_smoothed = dr_base * reb_ratio

	# --- STEP 4: MULTIPLIERS ---
	role_map = {"A (Franchise Player)": 1.20, "B (Core Starter)": 1.10, "C (Rotation Player)": 1.00, "D (Glue / Specialist)": 0.90}
	role_mult = role_map.get(target_role, 1.00)
	
	c_mults = { "PTS": 1.0, "REB": 1.0, "OR": 1.0, "DR": 1.0, "AST": 1.0, "STL": 1.0, "BLK": 1.0 }
	for mod_name in active_modifiers:
		if mod_name in SITUATIONAL_MODIFIERS:
			m = SITUATIONAL_MODIFIERS[mod_name]
			for target in m.get("targets", []):
				if target in c_mults: c_mults[target] *= m["factor"]

	dest_sys = dest_league["League"].split(' ')[0]
	orig_sys = origin_league["League"].split(' ')[0]
	cont_mult = 1.08 if (dest_sys == orig_sys and player_age < 30) else 1.00

	f_pts10 = pts_smoothed * role_mult * c_mults["PTS"] * cont_mult * age_mod
	f_reb10 = reb_smoothed * role_mult * c_mults["REB"] * cont_mult * age_mod
	f_or10 = or_smoothed * role_mult * c_mults["OR"] * cont_mult * age_mod
	f_dr10 = dr_smoothed * role_mult * c_mults["DR"] * cont_mult * age_mod
	f_ast10 = ast_base * role_mult * c_mults["AST"] * cont_mult * age_mod

	# --- STEP 5: SHOOTING ---
	two_p_diff = (origin_league["2P% Modifier"] / dest_league["2P% Modifier"]) ** 0.5
	# MULTIPLYING THE HEIGHT PENALTY INTO 2P%
	f_2p = raw_stats["FG%"] * two_p_diff * derived_height_mod_2p
	f_3p = raw_stats["3P%"] * (origin_league["3P% Modifier"] / dest_league["3P% Modifier"])

	# --- STEP 6: CEILING BONUSES ---
	delta = baseline_variability
	vol_scale = pace * role_mult * c_mults["PTS"] * cont_mult
	proj_3pa = (raw_stats["3PA"] / minutes) * target_min * vol_scale
	proj_fta = (raw_stats["FTA"] / minutes) * target_min * vol_scale
	proj_fga = (raw_stats["FGA"] / minutes) * target_min * vol_scale
	proj_2pa = proj_fga - proj_3pa

	bonus_skill = (proj_3pa * delta * 3 * f_3p) + (proj_2pa * delta * 2 * f_2p) + (proj_fta * delta * 1 * raw_stats["FT%"])
	bonus_phys_pts = ((f_pts10 / 10.0) * target_min) * delta
	size_floor = 0.22 * target_min if h_cm > 203 else 0
	
	base_proj = {
		"Min": target_min,
		"PTS": round((f_pts10 / 10.0) * target_min, 1),
		"REB": round((f_reb10 / 10.0) * target_min, 1),
		"OR": round((f_or10 / 10.0) * target_min, 1),
		"DR": round((f_dr10 / 10.0) * target_min, 1),
		"AS": round((f_ast10 / 10.0) * target_min, 1),
		"TO": round(((to10 * (origin_league["TO Modifier"]/dest_league["TO Modifier"]) * pace * 1.075) / 10.0) * target_min, 1),
		"STL": round(((stl10 * (origin_league["STL Modifier"]/dest_league["STL Modifier"]) * pace * role_mult * cont_mult) / 10.0) * target_min, 1),
		"BLK": round(((blk10 * (origin_league["BLK Modifier"]/dest_league["BLK Modifier"]) * pace * role_mult * cont_mult) / 10.0) * target_min, 1),
		"2P%": round(f_2p, 3), "3P%": round(f_3p, 3), "FT%": round(raw_stats["FT%"], 3),
		"bonus_skill": bonus_skill, "bonus_phys_pts": bonus_phys_pts, "size_floor": size_floor, 
		"ft_variance": delta, "twop_variance": delta, "threep_variance": 0.12 if delta < 0.12 else delta,
		"proj_3pa": round(proj_3pa, 1)
	}
	
	base_proj["VAL"] = round(base_proj["PTS"] + base_proj["REB"] + base_proj["AS"] + base_proj["STL"] + base_proj["BLK"] - base_proj["TO"] - (base_proj["PTS"] * 0.40), 1)
	
	# Returning all values up to the UI (so Risk Matrix can read the height modifiers)
	return base_proj, player_age, age_mod, is_defensive_glue, cont_mult, is_moving_up, rebound_height_modifier, derived_height_mod_2p, h_cm, origin_tier, dest_tier, 1.0
# ==========================================
# 9. SIDEBAR CONFIGURATION (DARK AREA)
# ==========================================
with st.sidebar:
	st.markdown("<h2 style='color:#E63946;'>InGame</h2>", unsafe_allow_html=True)
	st.markdown("<p style='color:#8E9AA6; font-size:12px;'>Basketball Analytics Platform</p>", unsafe_allow_html=True)
	st.markdown("---")
	
	menu_options = ["Home", "Conversion App"]
	default_index = menu_options.index(st.session_state.view_mode)
	
	view_mode_select = st.radio(
		"Navigation Menu",
		menu_options,
		index=default_index,
		key="view_mode_radio"
	)
	st.session_state.view_mode = view_mode_select
	
	# --- OPTION B: LEAGUE COEFFICIENT LOOKUP ---
	st.markdown("---")
	st.markdown("<h4 style='color:#FFFFFF; margin-bottom: 5px;'>League Coefficient Lookup</h4>", unsafe_allow_html=True)
	lookup_league_name = st.selectbox(
		"Select League to Inspect:", 
		leagues_df["League"].unique(), 
		key="lookup_league",
		label_visibility="collapsed"
	)
	lookup_row = leagues_df[leagues_df["League"] == lookup_league_name].iloc[0]
	
	st.markdown(
		f"""
		<div style="background-color: #1E2540; padding: 12px; border-radius: 5px; border: 1px solid #3B4B7A; color: #FFFFFF; font-size: 13px; line-height: 1.5; margin-top: 5px;">
			<b>Scouting Tier:</b> Tier {int(lookup_row['Tier'])}<br/>
			<b>Baseline Rate:</b> x{lookup_row['Rate']:.2f}<br/>
			<b>Pace:</b> {lookup_row['Pace']:.1f}<br/>
			<b>Confidence Rating (CS):</b> {lookup_row['CS']}<br/>
			<span style="color: #E63946; font-weight: bold; display: block; margin-top: 8px; margin-bottom: 2px;">Positional Modifiers:</span>
			• PGs: x{lookup_row['Slashers/PGs']:.2f}<br/>
			• Shooters: x{lookup_row['Shooters (SG/SF)']:.2f}<br/>
			• Bigs: x{lookup_row['Bigs (C/PF)']:.2f}
		</div>
		""",
		unsafe_allow_html=True
	)

	# --- OPTION A: PROSPECT SHORTLIST ---
	st.markdown("---")
	st.markdown("<h4 style='color:#FFFFFF; margin-bottom: 8px;'>Prospect Shortlist</h4>", unsafe_allow_html=True)
	if st.session_state.shortlist:
		for idx, item in enumerate(st.session_state.shortlist):
			st.markdown(
				f"""
				<div style="background-color: #1E2540; padding: 10px; border-radius: 5px; margin-bottom: 8px; border: 1px solid #3B4B7A;">
					<div style="color: #FFFFFF; font-weight: bold; font-size: 13px;">{item['name']}</div>
					<div style="color: #8E9AA6; font-size: 11px;">{item['orig_team']} ➔ {item['dest_league']}</div>
					<div style="color: #E63946; font-size: 12px; font-weight: bold; margin-top: 4px;">
						PTS: {item['pts']} | REB: {item['reb']} | VAL: {item['val']}
					</div>
				</div>
				""", 
				unsafe_allow_html=True
			)
		
		# Build Export DataFrame with comprehensive details
		shortlist_export_list = []
		for s in st.session_state.shortlist:
			shortlist_export_list.append({
				"Player Name": s["name"],
				"Original Team": s["orig_team"],
				"Original League": s["orig_league"],
				"Destination League": s["dest_league"],
				"Position Profile": s["position"],
				"Target Role Profile": s["target_role_label"], 
				"Origin Role Profile": s["origin_role_label"], 
				"Calculated Age": s["calculated_age"],
				"Height (cm)": s["height_cm"],
				"GP": s["gp"],
				
				# Baseline Inputs
				"Raw Baseline Min": s["raw_min"],
				"Raw Baseline PTS": s["raw_pts"],
				"Raw Baseline FGM": s["raw_fgm"],
				"Raw Baseline FGA": s["raw_fga"],
				"Raw Baseline FG%": s["raw_fg_pct"],
				"Raw Baseline 3PM": s["raw_3pm"],
				"Raw Baseline 3PA": s["raw_3pa"],
				"Raw Baseline 3P%": s["raw_3p_pct"],
				"Raw Baseline FTM": s["raw_ftm"],
				"Raw Baseline FTA": s["raw_fta"],
				"Raw Baseline FT%": s["raw_ft_pct"],
				"Raw Baseline OFF": s["raw_off"],
				"Raw Baseline DEF": s["raw_def"],
				"Raw Baseline TRB": s["raw_trb"],
				"Raw Baseline AST": s["raw_ast"],
				"Raw Baseline STL": s["raw_stl"],
				"Raw Baseline BLK": s["raw_blk"],
				"Raw Baseline TOV": s["raw_tov"],
				"Raw Baseline PF": s["raw_pf"],
				
				# Base Projections
				"Proj Base PTS": s["pts"],
				"Proj Base REB": s["reb"],
				"Proj Base OFF": s["proj_or"],
				"Proj Base DEF": s["proj_dr"],
				"Proj Base AST": s["proj_ast"],
				"Proj Base TOV": s["proj_tov"],
				"Proj Base STL": s["proj_stl"],
				"Proj Base BLK": s["proj_blk"],
				"Proj 2P%": s["proj_2p_pct"],
				"Proj 3P%": s["proj_3p_pct"],
				"Proj FT%": s["proj_ft_pct"],
				"Proj Base VAL": s["val"],
				
				# Proj Boundaries
				"Floor Projected PTS": s["floor_pts"],
				"Ceiling Projected PTS": s["ceil_pts"],
				"Floor Projected REB": s["floor_reb"],
				"Ceiling Projected REB": s["ceil_reb"],
				"Floor Projected AST": s["floor_ast"],
				"Ceiling Projected AST": s["ceil_ast"],
				"Floor Projected VAL": s["floor_val"],
				"Ceiling Projected VAL": s["ceil_val"],
				
				# Context & Role Ratings
				"Confidence Level (%)": s["conf_percent"],
				"Confidence Level Factors": s["conf_factor_str"],
				"Cumulative Context Multiplier": s["context_multiplier"],
				"Active Overrides": s["active_modifiers"],
				
				# Risk & Ratings
				"Risk Score (0-7)": s["risk_score"],
				"Risk Factor Badge": s["risk_badge"],
				"Data Confidence Rating (CS)": s["dest_cs"],
				"Player Badges": s["badges_str"],
				"Target Impact Rating (Stars)": s["stars_str"]
			})

		shortlist_df = pd.DataFrame(shortlist_export_list)
		csv_payload = shortlist_df.to_csv(index=False).encode('utf-8')
		
		# Combined Action Controls in Sidebar
		st.download_button(
			label="📥 Export Shortlist (CSV)",
			data=csv_payload,
			file_name="ingame_prospect_shortlist.csv",
			mime="text/csv",
			key="export_shortlist_csv",
			use_container_width=True
		)
		
		# Use on-click callback for safe state preservation on manual reset
		st.button(
			"Clear Shortlist", 
			key="clear_shortlist_btn", 
			on_click=clear_shortlist_callback, 
			use_container_width=True
		)
	else:
		st.markdown(
			"<p style='color: #8E9AA6; font-size: 12px; margin: 0;'>No pinned prospects yet. Pin players from the Projections tab.</p>", 
			unsafe_allow_html=True
		)

# ==========================================
# 10. MAIN CONTENT PANELS
# ==========================================
# Resolve variability before the projection engine runs
	dest_row = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]
	default_variability = abs(float(dest_row["T Low"])) if "T Low" in dest_row and not pd.isna(dest_row["T Low"]) else 0.20
	
	# Read from temporary widget state if it exists, otherwise fall back to persistent state
	variability = float(st.session_state.get("t_variability", st.session_state.p_variability))
	st.session_state.p_variability = variability
# ----------------- LANDING PAGE (HOME) -----------------
if st.session_state.view_mode == "Home":
	# Safely load and encode the local logo image
	img_base64 = None
	if os.path.exists("logo.png"):
		try:
			with open("logo.png", "rb") as image_file:
				img_base64 = base64.b64encode(image_file.read()).decode()
		except Exception:
			pass

	# If the image exists, render with inline Flexbox for vertical alignment and tight spacing
	if img_base64:
		header_html = f"""
		<div style="display: flex; align-items: center; gap: 15px; margin-bottom: 15px;">
			<img src="data:image/png;base64,{img_base64}" style="height: 70px; width: auto; object-fit: contain;" />
			<h1 style="color: #1F2937; margin: 0; font-size: 2.2rem; font-weight: 700; line-height: 1;">
				InGame: Performance Projection Engine
			</h1>
		</div>
		"""
		st.markdown(header_html, unsafe_allow_html=True)
	else:
		# Fallback to pure text if logo.png is not found
		st.markdown("<h1 style='color:#1F2937; margin: 0;'>InGame: Performance Projection Engine</h1>", unsafe_allow_html=True)
		
	st.markdown("<p style='color:#4B5563; font-size:18px;'>Translating competitive statistics across international and collegiate leagues with absolute statistical rigor.</p>", unsafe_allow_html=True)
	st.write("---")
	
	col1, col2 = st.columns([1.8, 1.2])
	
	with col1:
		st.markdown("### Performance Conversion Workspace")
		st.write(
			"Welcome, if you need a quickstart guide to use the app, expand the guide:"
		)
		
		# Collapsible usability guide that starts collapsed (expanded=False)
		with st.expander("How to Use the Conversion Tool", expanded=False):
			st.write(
				"InGame uses league coefficients, physical limits, and strategic situational overrides to "
				"translate a player's previous performance data into realistic projections for their next destination. "
				"To build an accurate projection, you will configure three primary areas inside the workspace:"
			)
			
			st.markdown("""
			**1. Load the Player's Profile & Stats**
			* *Search & Import:* Search a player's name via **RealGM** or paste a player URL from **FEB.es** (baloncestoenvivo.feb.es) to automatically pull in their historical statistics, birth year, height, and games played.
			* *Manual Entry:* If the player is not indexed, you can paste an average row from RealGM directly or manually edit their baseline numbers using the expandable stats table.
			
			**2. Set Up the Transition Dynamics**
			* *Original & Destination Leagues:* Select the league the player played in last season, and the target league they are signing with. The engine automatically calculates the pace, defensive physicality, and tier ratios between these divisions.
			* *Position Profile:* Choose the player's primary style (e.g., Slasher/PG, Slasher/D, Shooter, or Big). This assigns the appropriate positional defensive multipliers.
			* *Target Role Profile:* Select their projected rotation status in the new team (e.g., Franchise Player, Core Starter, Rotation Performer, or Glue/Specialist) to trigger the correct offensive usage scaling.
			* *Expected Minutes:* Use the slider to set the target on-court minutes per game to scale the absolute production numbers.
			
			**3. Evaluate Situational Modifiers & Auto-Scout**
			* Under the settings column, check the **Auto-Scout Alert** box. If the player's age, stats, or college origin trigger standard professional transitions, the system will recommend specific overrides (e.g., *Rookie Pro* or *Spacing Liability*) which you can apply with a single click.
			""")
			
		st.write("---")
		if st.button("Launch Conversion Workspace", key="launch_btn"):
			st.session_state.view_mode = "Conversion App"
			st.rerun()
			
	with col2:
		with st.container(border=True):
			st.markdown("### Supported Database Scope")
			st.write(f"Global Leagues Indexed: {len(leagues_df)}")
			st.write("International Tiers Mapped: Tier 1 to Tier 7")
			st.write("NCAA Scouting Modifiers: High-Major, Mid-Major, Low-Major, Division 2, Division 3")
			st.write("Contextual Modifiers available: 12 Situational Overrides")
			st.write("---")
			st.caption(
				"This engine implements strict statistical independence. Each estimated metric is "
				"modeled on its own performance variance curve."
			)

# ----------------- CONVERSION WORKSPACE -----------------
else:
    
	# 1. Resolve all persistent inputs from widget state (t_ keys) or safe persistent state (p_ keys) first
	player_name = st.session_state.get("t_player_name", st.session_state.p_player_name)
	orig_team = st.session_state.get("t_orig_team", st.session_state.p_orig_team)
	yob = int(st.session_state.get("t_yob", st.session_state.p_yob))
	height = int(st.session_state.get("t_height", st.session_state.p_height))
	gp = int(st.session_state.get("t_gp", st.session_state.p_gp))
	position = st.session_state.get("t_position", st.session_state.p_position)
	st.session_state.p_position = position  # Lock it into persistent state
	orig_league_name = st.session_state.get("t_orig_league_name", st.session_state.p_orig_league_name)
	dest_league_name = st.session_state.get("t_dest_league_name", st.session_state.p_dest_league_name)
	
	# --- BULLETPROOF VARIABILITY MAPPING (Bypassing T Low / T High) ---
	cs_var_map = {"High": 0.05, "Medium": 0.10, "Low": 0.15, "Very Low": 0.20, "Estimated": 0.25}
	
	orig_row = leagues_df[leagues_df["League"] == orig_league_name].iloc[0]
	dest_row = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]
	
	orig_cs = orig_row["CS"]
	dest_cs = dest_row["CS"]
	
	# The engine takes the WORST confidence (highest variance) between the two leagues
	combined_default_variability = max(cs_var_map.get(orig_cs, 0.20), cs_var_map.get(dest_cs, 0.20))
	combined_cs_display = f"{orig_cs} -> {dest_cs}"

	# Auto-reset the slider if the leagues change
	if orig_league_name != st.session_state.p_orig_league_name or dest_league_name != st.session_state.p_dest_league_name:
		st.session_state.p_variability = combined_default_variability
		if "t_variability" in st.session_state:
			del st.session_state["t_variability"]

	default_variability = combined_default_variability
			
	target_role = st.session_state.get("t_target_role", st.session_state.p_target_role)
	target_min = float(st.session_state.get("t_target_min", st.session_state.p_target_min))
	selected_modifiers = st.session_state.get("t_selected_modifiers", st.session_state.p_selected_modifiers)
	role_mod = float(st.session_state.get("t_role_mod", st.session_state.p_role_mod))
	orig_team_quality = st.session_state.get("t_orig_team_quality", st.session_state.p_orig_team_quality)
	# 2. Sync variables back into persistent slots so they are up-to-date across tab switches
	st.session_state.p_player_name = player_name
	st.session_state.p_orig_team = orig_team
	st.session_state.p_yob = yob
	st.session_state.p_height = height
	st.session_state.p_gp = gp
	st.session_state.p_position = position
	st.session_state.p_orig_league_name = orig_league_name
	st.session_state.p_dest_league_name = dest_league_name
	st.session_state.p_target_role = target_role
	st.session_state.p_target_min = target_min
	st.session_state.p_selected_modifiers = selected_modifiers
	st.session_state.p_role_mod = role_mod
	st.session_state.p_orig_team_quality = orig_team_quality
	# Read from temporary widget state if it exists (divided by 100 to convert to decimal), otherwise fall back to persistent state
	if "t_variability" in st.session_state:
		variability = float(st.session_state.t_variability) / 100.0
	else:
		variability = float(st.session_state.p_variability)
		
	st.session_state.p_variability = variability

	# PRE-INITIALIZE ALL PROJECTION VARIABLES TO PREVENT STATIC ANALYSIS ERRORS
	proj = {
		"Min": target_min, "PTS": 0.0, "REB": 0.0, "OR": 0.0, "DR": 0.0, "AS": 0.0, "TO": 0.0, "STL": 0.0, "BLK": 0.0,
		"2P%": 0.0, "3P%": 0.0, "FT%": 0.0, "FTR": 0.0, "VAL": 0.0,
		"ft_variance": 0.15, "twop_variance": 0.15, "threep_variance": 0.15
	}
	calculated_age = 2026 - yob
	total_context_multiplier = 1.00
	is_defensive_glue = False
	continuity_mod = 1.00
	is_moving_up = False
	rebound_height_modifier = 1.00
	derived_height_mod_2p = 1.00
	h_cm = 185.0
	origin_tier = 5.0
	dest_tier = 5.0
	overall_league_mod = 1.00
	orig_league = leagues_df[leagues_df["League"] == "Spain Segunda FEB"].iloc[0]
	dest_league = leagues_df[leagues_df["League"] == "Spain Segunda FEB"].iloc[0]

	try:
		orig_league = leagues_df[leagues_df["League"] == orig_league_name].iloc[0]
		dest_league = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]

		(
			proj, 
			calculated_age, 
			total_context_multiplier, 
			is_defensive_glue, 
			continuity_mod, 
			is_moving_up, 
			rebound_height_modifier, 
			derived_height_mod_2p, 
			h_cm, 
			origin_tier, 
			dest_tier, 
			overall_league_mod
		) = run_projection_engine(
			st.session_state.raw_stats, orig_league, dest_league, role_mod, position,
			yob, height, 1.00, 1.00, 1.00,
			selected_modifiers, target_min, target_role, variability,
			orig_team_quality
		)
	except Exception as e:
		st.error(f"Calculation Error: {e}") # This will tell you exactly what is breaking

	# 2. Render Tab Structure
	tab_stats_editor, tab_projections, tab_rules_warnings = st.tabs([
		"Raw Statistics Editor and Settings",
		"Performance Projections", 
		"Context Rules and Warnings"
	])

	# ----------------- TAB 1: RAW STATISTICS EDITOR & SETTINGS -----------------
	with tab_stats_editor:
		col_setup, col_stats = st.columns([1.6, 1.4])
		
		with col_setup:
			st.markdown("### Player Profile and Transition Settings")
			subcol_profile, subcol_transition = st.columns(2)
			
			with subcol_profile:
				st.markdown("##### Player Profile")
				player_name_widget = st.text_input("Player Name", value=st.session_state.p_player_name, key="t_player_name")
				st.session_state.p_player_name = player_name_widget

				orig_team_widget = st.text_input("Current Team / School", value=st.session_state.p_orig_team, key="t_orig_team")
				st.session_state.p_orig_team = orig_team_widget
				
				# ---> NEW TEAM QUALITY DROPDOWN <---
				options_team_qual = ["Top of League / Contender", "Mid-Table (Default)", "Bottom / Relegation"]
				idx_qual = options_team_qual.index(st.session_state.p_orig_team_quality) if st.session_state.p_orig_team_quality in options_team_qual else 1
				st.markdown("<div style='font-weight: 700; color: #E63946; font-size: 14px; margin-top: 12px; margin-bottom: 4px;'>Origin Team Quality</div>", unsafe_allow_html=True)
				orig_team_qual_widget = st.selectbox(
					"Origin Team Quality",
					options_team_qual,
					index=idx_qual,
					key="t_orig_team_quality",
					label_visibility="collapsed"
				)
				st.session_state.p_orig_team_quality = orig_team_qual_widget

				yob_widget = st.number_input("Year of Birth", min_value=1970, max_value=2015, value=int(st.session_state.p_yob), step=1, key="t_yob")

				height_widget = st.number_input("Height (cm)", min_value=0, max_value=230, value=int(st.session_state.p_height), step=1, key="t_height")
				st.session_state.p_height = height_widget
				
				if st.session_state.p_height == 0:
					st.warning("⚠️ Height not detected. Please enter it manually for accurate projections.")

				gp_widget = st.number_input("Games Played (GP)", min_value=1, max_value=100, value=int(st.session_state.p_gp), step=1, key="t_gp")
				st.session_state.p_gp = gp_widget
				
				# Highlighted Position Profile Selectbox
				st.markdown(
					"<div style='font-weight: 700; color: #E63946; font-size: 14px; margin-top: 12px; margin-bottom: 4px;'>Position Profile *</div>", 
					unsafe_allow_html=True
				)
				options_pos = ["Slashers/PGs", "Shooters (SG/SF)", "Bigs (C/PF)", "Slashers / D(Non-Shooters)"]
				idx_pos = options_pos.index(st.session_state.p_position) if st.session_state.p_position in options_pos else 0
				position_widget = st.selectbox(
					"Position Profile", 
					options_pos,
					index=idx_pos,
					key="t_position",
					label_visibility="collapsed"
				)
				st.session_state.p_position = position_widget
				
				st.markdown("---")
				st.markdown("##### NCAA D1 Scouting Lookup")
				conf_options = ["Search Conference..."] + list(NCAA_CONFERENCE_MAPPING.keys())
				selected_conf = st.selectbox("Search NCAA Conference:", conf_options)
				if selected_conf != "Search Conference...":
					mapped_tier = NCAA_CONFERENCE_MAPPING[selected_conf]
					st.info(f"Recommended Scouting Setting:\n{mapped_tier}")
				
			with subcol_transition:
				st.markdown("##### League and Context Settings")
				
				# Highlighted Original League Selectbox
				st.markdown(
					"<div style='font-weight: 700; color: #E63946; font-size: 14px; margin-top: 12px; margin-bottom: 4px;'>Original League *</div>", 
					unsafe_allow_html=True
				)
				options_orig = list(leagues_df["League"].unique())
				idx_orig = options_orig.index(st.session_state.p_orig_league_name) if st.session_state.p_orig_league_name in options_orig else 0
				orig_league_widget = st.selectbox(
					"Original League", 
					options_orig, 
					index=idx_orig,
					key="t_orig_league_name",
					label_visibility="collapsed"
				)
				st.session_state.p_orig_league_name = orig_league_widget
				
				# Highlighted Destination League Selectbox
				st.markdown(
					"<div style='font-weight: 700; color: #E63946; font-size: 14px; margin-top: 12px; margin-bottom: 4px;'>Destination League *</div>", 
					unsafe_allow_html=True
				)
				options_dest = list(leagues_df["League"].unique())
				idx_dest = options_dest.index(st.session_state.p_dest_league_name) if st.session_state.p_dest_league_name in options_dest else 0
				dest_league_widget = st.selectbox(
					"Destination League", 
					options_dest, 
					index=idx_dest,
					key="t_dest_league_name",
					label_visibility="collapsed"
				)
				st.session_state.p_dest_league_name = dest_league_widget
				
				# Resolve destination league dynamic confidence parameters safely inside this block
				dest_row = leagues_df[leagues_df["League"] == dest_league_name].iloc[0]
				dest_cs = dest_row["CS"]
				default_variability = abs(float(dest_row["T Low"])) if "T Low" in dest_row and not pd.isna(dest_row["T Low"]) else 0.20
				
				# Highlighted Target Role Profile Selectbox
				st.markdown(
					"<div style='font-weight: 700; color: #E63946; font-size: 14px; margin-top: 12px; margin-bottom: 4px;'>Target Role Profile *</div>", 
					unsafe_allow_html=True
				)
				options_role = ["A (Franchise Player)", "B (Core Starter)", "C (Rotation Player)", "D (Glue / Specialist)"]
				idx_role = options_role.index(st.session_state.p_target_role) if st.session_state.p_target_role in options_role else 0
				target_role_widget = st.selectbox(
					"Target Role Profile", 
					options_role, 
					index=idx_role,
					key="t_target_role",
					label_visibility="collapsed"
				)
				st.session_state.p_target_role = target_role_widget
				
				target_min_widget = st.slider("Expected Played Minutes (Min)", 5.0, 40.0, value=float(st.session_state.p_target_min), step=0.5, key="t_target_min")
				st.session_state.p_target_min = target_min_widget
				
				# Prominent Active Situational Modifiers placed in context setup
				selected_modifiers_widget = st.multiselect(
					"Active Situational Modifiers",
					options=list(SITUATIONAL_MODIFIERS.keys()),
					default=st.session_state.p_selected_modifiers,
					key="t_selected_modifiers"
				)
				st.session_state.p_selected_modifiers = selected_modifiers_widget
				
				# Live dynamic Auto-Scout recommendations
				live_suggestions = auto_detect_situational_modifiers(
					st.session_state.raw_stats, yob, height, gp, 
					orig_league_name, dest_league_name, position
				)
				
				if live_suggestions:
					unselected_suggestions = [s for s in live_suggestions if s not in selected_modifiers]
					if unselected_suggestions:
						st.info(f"💡 **Auto-Scout Alert:** Based on current metrics, we recommend activating: **{', '.join(unselected_suggestions)}**.")
						st.button(
							"Apply Suggested Modifiers", 
							key="apply_suggestions_btn",
							on_click=apply_suggestions_callback,
							args=(unselected_suggestions,)
						)
						
				# Collapsible Situational Modifiers Explanation Guide
				with st.expander("ℹ️ Situational Modifiers Guide", expanded=False):
					st.caption("Apply these contextual overrides to scale statistical volume metrics based on specific transition and roster dynamics:")
					for mod_name, mod_info in SITUATIONAL_MODIFIERS.items():
						if mod_name == "Import Boost":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: In divisions like Primera and Segunda FEB with strict roster limits "
								f"signed foreign imports are brought in "
								f"to be primary offensive focal points. This structural expectation results in a higher "
								f"possession share, justifying an implicit 12% scaling boost to volume.*"
							)
						elif mod_name == "Rookie Pro":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: The standard transition penalty for prospects transitioning from college/academy to senior professional leagues.*"
							)
						elif mod_name == "Veteran Leader":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Experienced pros (Age 30+) descending to lower-tier divisions.*"
							)
						elif mod_name == "NCAA Low As-High Vol":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Applied to college scorers who carried heavy scoring volume but recorded low assists.*"
							)
						elif mod_name == "Instability Penalty":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: For players who have played for 3 or more teams in the last 3 seasons.*"
							)
						elif mod_name == "Alpha Shadow":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Positional blockage from established stars.*"
							)
						elif mod_name == "Spacing Liability":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Perimeter players shooting poorly from three on volume.*"
							)
						elif mod_name == "Late Arrival":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Mid-season replacements signing after January 1st.*"
							)
						elif mod_name == "Socialist System":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Coaches operating egalitarian, deep rotations.*"
							)
						elif mod_name == "FTR / Whistle Tax":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: High Free Throw Rates from lower divisions may drop in elite leagues.*"
							)
						elif mod_name == "Cantera TOP":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Elite academy graduates joining standard senior rosters.*"
							)
						elif mod_name == "Tier Fatigue":
							st.markdown(
								f"**{mod_name} (x{mod_info['factor']:.2f}):** {mod_info['desc']} "
								f"\n*Scouting Note: Stagnation penalty for prolonged tier exposure.*"
							)

			# Advanced Calibration parameters tucked into a bottom expander
			st.markdown("---")
			with st.expander("Model Calibration (Advanced)", expanded=False):
				st.caption(f"Fine-tune model mathematical weights and statistical variability boundaries. Data Confidence Score (CS): **{dest_cs}**.")
				role_mod_widget = st.slider("Role Multiplier Scale", 0.80, 1.30, step=0.05, key="role_mod")
				
				variability_slider_key = f"variability_slider_{dest_league_name}"
				# Use a unique slider key and map value to persistent state
				variability_val = st.slider(
					"Statistical Variability (%)", 
					min_value=5.0, 
					max_value=30.0, 
					value=float(st.session_state.p_variability * 100.0), 
					step=1.0,
					key="t_variability"
				)
				st.session_state.p_variability = variability_val / 100.0
		with col_stats:
			st.markdown("### Raw Stats Importer and Editor")
			
			# RealGM Player Search Panel (Collapsible Expander)
			with st.expander("🔍 Import from RealGM Database", expanded=False):
				st.caption("Enter a player's name to fetch their statistics and profile directly from RealGM.")
				search_query = st.text_input("Enter Player Name:", placeholder="e.g. Roger Fabrega")
				
				if "search_results" not in st.session_state:
					st.session_state.search_results = None
				if "selected_player_url" not in st.session_state:
					st.session_state.selected_player_url = None
				if "parsed_player_profile" not in st.session_state:
					st.session_state.parsed_player_profile = None

				if st.button("Search RealGM"):
					if search_query:
						with st.spinner("Searching RealGM database..."):
							results, error = search_realgm_players(search_query)
							if error:
								st.error(error)
							else:
								st.session_state.search_results = results
								st.session_state.selected_player_url = None
								st.session_state.parsed_player_profile = None
								
				if st.session_state.search_results:
					results = st.session_state.search_results
					if results.get("direct_match"):
						st.session_state.selected_player_url = results["url"]
						if not st.session_state.parsed_player_profile:
							profile, parse_err = parse_player_summary(results["url"], results["html"])
							if parse_err:
								st.error(parse_err)
							else:
								st.session_state.parsed_player_profile = profile
					else:
						st.info(f"Found {len(results['results'])} matching profiles. Please select the correct candidate:")
						options_list = [f"{r['name']} ({r['details']})" for r in results["results"]]
						selected_label = st.selectbox("Select Candidate:", options_list)
						selected_index = options_list.index(selected_label)
						candidate_url = results["results"][selected_index]["url"]
						
						if st.button("Load Selected Candidate"):
							st.session_state.selected_player_url = candidate_url
							with st.spinner("Fetching player summary..."):
								profile, parse_err = parse_player_summary(candidate_url)
								if parse_err:
									st.error(parse_err)
								else:
									st.session_state.parsed_player_profile = profile
									
				if st.session_state.parsed_player_profile:
					profile = st.session_state.parsed_player_profile
					st.success(f"Profile Loaded! Height: {profile['height_cm']}cm | Born: {profile['yob']}")
					
					table_names = [t["name"] for t in profile["tables"]]
					selected_table_name = st.selectbox("Select Stat Table:", table_names)
					table_index = table_names.index(selected_table_name)
					selected_table = profile["tables"][table_index]
					df = selected_table["df"]
					
					row_labels = []
					for i, row in df.iterrows():
						season_val = row.get("SEASON", row.get("YEAR", f"Row {i}"))
						team_val = row.get("TEAM", row.get("SCHOOL", "Unknown Team"))
						league_val = row.get("LEAGUE", row.get("CONFERENCE", "NCAA" if "SCHOOL" in df.columns else "Unknown League"))
						
						gp_val = row.get("GP", 0)
						try:
							gp_int = int(float(str(gp_val).strip()))
						except (ValueError, TypeError):
							gp_int = 0
							
						row_labels.append(f"{season_val} | {team_val} | {league_val} ({gp_int} GP)")
						
					selected_row_label = st.selectbox("Select Season Row:", row_labels)
					row_index = row_labels.index(selected_row_label)
					selected_row = df.iloc[row_index]
					
					p_name = search_query
					if st.session_state.search_results and not st.session_state.search_results.get("direct_match"):
						p_name = results["results"][selected_index]["name"]
					
					p_team = selected_row.get("TEAM", selected_row.get("SCHOOL", "Unknown Team"))
						
					st.button(
					"Import Selected Stats into Workspace",
					on_click=load_scraped_row_into_state, # <--- FIX: Use the scraped row function
					args=(selected_row, p_name, profile['height_cm'], profile['yob'], p_team)
				)

			# FEB.es Player Import container (Collapsible Expander)
			with st.expander("🇪🇸 Import from FEB.es (Spain)", expanded=False):
				st.caption("Paste a player statistics URL from baloncestoenvivo.feb.es or competiciones.feb.es to load domestic Spanish stats.")
				feb_url_input = st.text_input("Enter FEB Player URL:", placeholder="https://baloncestoenvivo.feb.es/jugador/...")
				
				if "feb_parsed_profile" not in st.session_state:
					st.session_state.feb_parsed_profile = None
					
				if st.button("Load FEB Profile"):
					if feb_url_input:
						with st.spinner("Fetching FEB database..."):
							feb_profile, feb_err = parse_feb_player_profile(feb_url_input)
							if feb_err:
								st.error(feb_err)
							else:
								st.session_state.feb_parsed_profile = feb_profile
								if "shooting_debug" in st.session_state:
									del st.session_state.shooting_debug # CLEAR OLD DEBUG				
				if st.session_state.feb_parsed_profile:
					f_profile = st.session_state.feb_parsed_profile
					if not f_profile.get("rows"):
						st.warning("Could not find career stats. Try another URL.")
					else:
						# 1. Prepare the dropdown labels
						row_labels = [r["label"] for r in f_profile["rows"]]
						
						# 2. Let the user select the row
						selected_idx = st.selectbox("Select Season:", range(len(row_labels)), format_func=lambda x: row_labels[x])
						
						# 3. DEFINE THE VARIABLES (This fixes your UndefinedVariable error)
						selected_data = f_profile["rows"][selected_idx]
						selected_row = selected_data["stats"]
						selected_team = selected_data["equipo"]
						actual_name = f_profile.get("player_name", "FEB Player")
						# NEW: Get Metadata from profile
						scraped_height = f_profile.get("height_cm", 185)
						scraped_yob = f_profile.get("yob", 2005)
						
						# 4. Extract GP for calculation
						try:
							gp_num = float(str(selected_row.get("PART", "1")).replace(",", "."))
						except:
							gp_num = 1.0

						# 5. The Import Button
						st.button(
							f"Import {row_labels[selected_idx]}",
                            on_click=load_feb_row_into_state,
                            args=(
                                selected_row, 
                                actual_name, 
                                scraped_height, 
                                scraped_yob, 
                                selected_team, 
                                gp_num,
                                row_labels[selected_idx], # <--- PASS LABEL
                                f_profile["rows"]         # <--- PASS HISTORY
                            )
                        )
			# RealGM Manual Row Importer
			with st.expander("RealGM Manual Row Importer", expanded=False):
				st.caption("Fallback: Highlight and copy the averages line from RealGM starting with GP and GS, then paste below.")
				pasted_line = st.text_input(
					"Paste RealGM row here:",
					placeholder="26\t26\t30.2\t18.2\t5.7\t12.5\t.458...",
					key="manual_paste_input"
				)
				
				if st.button("Parse and Apply Copied Stats"):
					if pasted_line:
						parsed_stats, parsed_gp, error = parse_realgm_row(pasted_line)
						if error:
							st.error(error)
						else:
							st.session_state.raw_stats = parsed_stats
							st.session_state.gp = parsed_gp
							st.success("Successfully parsed and updated active statistics!")
							st.rerun()
		if "shooting_debug" in st.session_state:
			with st.expander("🔬 Shooting Baseline Debugger", expanded=True):
				st.info(st.session_state.shooting_debug)
				st.caption("Math: (Current Season % * 0.6) + (Historical Career % * 0.4)")
			# Raw Metric Tuning
			with st.expander("Raw Metric Tuning", expanded=False):
				st.caption("Manually adjust individual raw workspace parameters.")
				ordered_metrics = ["MIN", "PTS", "FGM", "FGA", "FG%", "3PM", "3PA", "3P%", "FTM", "FTA", "FT%", "OFF", "DEF", "TRB", "AST", "STL", "BLK", "TOV", "PF"]
				df_rows = [{"Metric": metric, "Value": float(st.session_state.raw_stats.get(metric, 0.0))} for metric in ordered_metrics]
				df_stats = pd.DataFrame(df_rows)
				
				edited_df = st.data_editor(
					df_stats,
					column_config={
						"Metric": st.column_config.TextColumn("Metric", disabled=True),
						"Value": st.column_config.NumberColumn("Value", min_value=0.0, step=0.001, format="%.3f")
					},
					disabled=["Metric"],
					hide_index=True,
					use_container_width=True,
					height=260
				)
				
				active_raw_stats = dict(zip(edited_df["Metric"], edited_df["Value"]))
				active_raw_stats["FG%"] = active_raw_stats["FGM"] / active_raw_stats["FGA"] if active_raw_stats["FGA"] > 0 else 0.0
				active_raw_stats["3P%"] = active_raw_stats["3PM"] / active_raw_stats["3PA"] if active_raw_stats["3PA"] > 0 else 0.0
				active_raw_stats["FT%"] = active_raw_stats["FTM"] / active_raw_stats["FTA"] if active_raw_stats["FTA"] > 0 else 0.0
				st.session_state.raw_stats = active_raw_stats

# ----------------- TAB 2: PERFORMANCE PROJECTIONS -----------------
	with tab_projections:
		# Calculate active coefficients for display
		display_pts_mult = orig_league[position] / dest_league[position]
		league_reb_ratio = orig_league["Rebounds"] / dest_league["Rebounds"]
		pos_reb_adj = 1.15 if position == "Bigs (C/PF)" else (0.90 if "PG" in position or "Slasher" in position else 1.0)
		display_reb_mult = league_reb_ratio * pos_reb_adj

		# Build the Modifiers string
		mod_strings = []
		for m_name in selected_modifiers:
			if m_name in SITUATIONAL_MODIFIERS:
				factor = SITUATIONAL_MODIFIERS[m_name]['factor']
				mod_strings.append(f"{m_name} (x{factor:.2f})")
		
		# Append the dynamic Age Modifier if applicable
		if total_context_multiplier != 1.0:
			mod_strings.append(f"Age Curve (x{total_context_multiplier:.2f})")
			
		# Append the dynamic System Continuity Modifier if applicable
		if continuity_mod > 1.0:
			mod_strings.append(f"System Continuity (x{continuity_mod:.2f})")
		
		mods_display = " | ".join(mod_strings) if mod_strings else "None Active"

		# Render the CSS Grid diagnostic card
		st.markdown(
			f"""
			<div style="background-color: #f8f9fa; padding: 15px; border-radius: 6px; border-left: 5px solid #E63946; margin-bottom: 20px; font-size: 13px; line-height: 1.6;">
				<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px;">
					<div>
						<b style="color:#11152C; font-size: 13px;">📈 Volume & Pace Scaling:</b><br/>
						• Points Scaling: <code style="color:#E63946; font-weight:bold;">x{display_pts_mult:.2f}</code><br/>
						• Pace Adjustment: <code style="color:#E63946; font-weight:bold;">x{dest_league['Pace']/orig_league['Pace']:.2f}</code>
					</div>
					<div>
						<b style="color:#11152C; font-size: 13px;">🏀 Rebound Scaling:</b><br/>
						• Total Rebound Mult: <code style="color:#E63946; font-weight:bold;">x{display_reb_mult * rebound_height_modifier:.2f}</code><br/>
						<span style="color:#4B5563; font-size: 11px;">(League: {league_reb_ratio:.2f} * Pos: {pos_reb_adj:.2f} * Size: {rebound_height_modifier:.2f})</span>
					</div>
					<div>
						<b style="color:#11152C; font-size: 13px;">📏 Physical Sizing Scaling:</b><br/>
						• Rebound Modifier: <code style="color:#E63946; font-weight:bold;">x{rebound_height_modifier:.2f}</code><br/>
						• 2P% Modifier: <code style="color:#E63946; font-weight:bold;">x{derived_height_mod_2p:.2f}</code>
					</div>
				</div>
				<div style="border-top: 1px solid #dee2e6; padding-top: 10px; margin-top: 10px;">
					<b style="color:#11152C;">Active Situational Overrides:</b> 
					<span style="color: #4B5563; font-weight: 500;">{mods_display}</span>
				</div>
			</div>
			""", 
			unsafe_allow_html=True
		)
		st.markdown(f"### Conversion Projections: {player_name}")
		st.caption(f"Calculated Age: {calculated_age} | Height: {height} cm | Target Minutes: {target_min} min | Target Position: {position} ({orig_league_name} ➔ {dest_league_name})")

		# --- RE-DEFINE HELPER FUNCTIONS (Fixes UndefinedVariable errors) ---
		def apply_var(val, var, direction=1):
			return round(val * (1 + (var * direction)), 1)

		def f_pct(val):
			return f"{val * 100:.1f}%"

		# --- TAB 2: VARIABLE CALCULATIONS ---
		pts_base = proj["PTS"]
		pts_ceil = round(pts_base + proj["bonus_skill"] + proj["bonus_phys_pts"], 1)
		pts_floor = round(max(0.0, pts_base - proj["bonus_skill"] - proj["bonus_phys_pts"]), 1)

		reb_base = proj["REB"]
		reb_ceil = round(max(reb_base * (1 + variability), proj["size_floor"]), 1)
		reb_floor = round(reb_base * (1 - variability), 1)
		
		ratio_ceil = reb_ceil / reb_base if reb_base > 0 else 1.0
		ratio_floor = reb_floor / reb_base if reb_base > 0 else 1.0
		
		or_base, dr_base = proj["OR"], proj["DR"]
		or_ceil, or_floor = round(or_base * ratio_ceil, 1), round(or_base * ratio_floor, 1)
		dr_ceil, dr_floor = round(dr_base * ratio_ceil, 1), round(dr_base * ratio_floor, 1)

		# Other stats
		as_base, stl_base, blk_base, to_base = proj["AS"], proj["STL"], proj["BLK"], proj["TO"]
		as_ceil, as_floor = apply_var(as_base, variability, 1), apply_var(as_base, variability, -1)
		stl_ceil, stl_floor = apply_var(stl_base, variability, 1), apply_var(stl_base, variability, -1)
		blk_ceil, blk_floor = apply_var(blk_base, variability, 1), apply_var(blk_base, variability, -1)
		to_ceil, to_floor = apply_var(to_base, -variability, 1), apply_var(to_base, -variability, -1) 

		val_base = proj["VAL"]
		val_ceil = apply_var(val_base, variability, 1)
		val_floor = apply_var(val_base, variability, -1)

		# --- COMPLETE TABLE DATA DEFINITION ---
		transposed_rows = [
			{"Metric": "PTS (Points)", "Floor": f"{pts_floor:.1f}", "BASE PROJECTION": f"{pts_base:.1f}", "Ceiling": f"{pts_ceil:.1f}", "Active Variance": "Skill + Phys"},
			{"Metric": "REB (Total Rebounds)", "Floor": f"{reb_floor:.1f}", "BASE PROJECTION": f"{reb_base:.1f}", "Ceiling": f"{reb_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "DR (Def. Rebounds)", "Floor": f"{dr_floor:.1f}", "BASE PROJECTION": f"{dr_base:.1f}", "Ceiling": f"{dr_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "OR (Off. Rebounds)", "Floor": f"{or_floor:.1f}", "BASE PROJECTION": f"{or_base:.1f}", "Ceiling": f"{or_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "AST (Assists)", "Floor": f"{as_floor:.1f}", "BASE PROJECTION": f"{as_base:.1f}", "Ceiling": f"{as_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "TO (Turnovers)", "Floor": f"{to_floor:.1f}", "BASE PROJECTION": f"{to_base:.1f}", "Ceiling": f"{to_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "STL (Steals)", "Floor": f"{stl_floor:.1f}", "BASE PROJECTION": f"{stl_base:.1f}", "Ceiling": f"{stl_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "BLK (Blocks)", "Floor": f"{blk_floor:.1f}", "BASE PROJECTION": f"{blk_base:.1f}", "Ceiling": f"{blk_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "2P% (2-Point %)", "Floor": f_pct(max(0.0, proj["2P%"] * (1 - variability))), "BASE PROJECTION": f_pct(proj["2P%"]), "Ceiling": f_pct(min(0.80, proj["2P%"] * (1 + variability))), "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "3P% (3-Point %)", "Floor": f_pct(max(0.0, proj["3P%"] * (1 - 0.12))), "BASE PROJECTION": f_pct(proj["3P%"]), "Ceiling": f_pct(min(0.55, proj["3P%"] * (1 + 0.12))), "Active Variance": "± 12%"},
			{"Metric": "FT% (Free Throw %)", "Floor": f_pct(max(0.0, proj["FT%"] * (1 - variability))), "BASE PROJECTION": f_pct(proj["FT%"]), "Ceiling": f_pct(min(1.00, proj["FT%"] * (1 + variability))), "Active Variance": f"± {variability*100:.0f}%"},
			{"Metric": "VAL (PIR / Rating)", "Floor": f"{val_floor:.1f}", "BASE PROJECTION": f"{val_base:.1f}", "Ceiling": f"{val_ceil:.1f}", "Active Variance": f"± {variability*100:.0f}%"}
		]

		# Set up a tighter, balanced split layout for the projections tab
		col_table, col_details = st.columns([1.4, 1.0])

		with col_table:
			df_output = pd.DataFrame(transposed_rows).set_index("Metric")
			styled_df = df_output.style.apply(highlight_scouting_outliers, axis=1)

			# Updated dataframe view with clean, compact, pixel-precise sizing
			st.dataframe(
				styled_df,
				use_container_width=False, # Prevents horizontal stretching
				height=500,
				column_config={
					"Floor": st.column_config.TextColumn(width=110),
					"BASE PROJECTION": st.column_config.TextColumn("PROJECTION", help="The most likely output", width=110),
					"Ceiling": st.column_config.TextColumn(width=110),
					"Active Variance": st.column_config.TextColumn("VAR", width=90)
				}
			)

		with col_details:
			# Context and Roles container card
			with st.container(border=True):
				st.markdown("#### 🎭 Context and Roles")
				origin_role_label = "D - From Glue/Connector (<6 FGA)" if is_defensive_glue else "C/B - Rotational/Core Performer"
				st.write(f"**Origin Role:** `{origin_role_label}`")
				
				target_role_label = TARGET_ROLE_CONFIGS.get(target_role, target_role)
				st.write(f"**Target Role:** `{target_role_label}`")
				
				# Confidence Level calculation
				conf_percent = 80
				conf_factors = []
				if (is_defensive_glue and "D (Glue" not in target_role) or (not is_defensive_glue and "D (Glue" in target_role):
					conf_percent -= 5
					conf_factors.append("🔄 New Role")
				if calculated_age < 23 or calculated_age > 33:
					conf_percent -= 5
					conf_factors.append("⚠️ Age Factor")
					
				conf_factor_str = " | ".join(conf_factors) if conf_factors else "Baseline stable"
				st.write(f"**Confidence Level:** `{conf_percent}%: {conf_factor_str}`")

			# Risk and Ratings container card
			with st.container(border=True):
				st.markdown("#### ⚖️ Risk and Ratings")
				pf_per_40 = (st.session_state.raw_stats["PF"] / st.session_state.raw_stats["MIN"]) * 40 if st.session_state.raw_stats["MIN"] > 0 else 0.0
				
				# 1. Val_CS (Unreliable Data Matrix)
				cs_map = {"Estimated": 1, "Very Low": 2, "Low": 3, "Medium": 4, "High": 5}
				orig_cs_val = cs_map.get(orig_league["CS"], 1)
				dest_cs_val = cs_map.get(dest_league["CS"], 1)
				min_cs_val = min(orig_cs_val, dest_cs_val)
				
				if min_cs_val <= 2: val_cs = "Very Low"
				elif min_cs_val == 3: val_cs = "Low"
				else: val_cs = "High"
				
				# 2. Val_Shoot (Shooting Fluke / Gem Matrix)
				three_pct = st.session_state.raw_stats.get("3P%", 0)
				ft_pct = st.session_state.raw_stats.get("FT%", 0)
				is_fluke = three_pct > 0.38 and ft_pct < 0.65
				is_gem = three_pct < 0.32 and ft_pct > 0.82
				
				# --- EXACT EXCEL RISK CALCULATION ---
				risk_foul = 1.0 if pf_per_40 > 5.5 else 0.0
				risk_size = 1.0 if (rebound_height_modifier < 1.0 or derived_height_mod_2p < 1.0) else 0.0
				risk_team = 1.0 if orig_team_quality == "Bottom / Relegation" else 0.0
				risk_rust = 1.0 if gp < 12 else 0.0
				
				if val_cs == "Very Low": risk_cs = 1.5
				elif val_cs == "Low": risk_cs = 0.5
				else: risk_cs = 0.0
					
				if is_fluke: risk_shoot = 1.5
				elif is_gem: risk_shoot = -0.5
				else: risk_shoot = 0.0
					
				total_risk_score = risk_foul + risk_size + risk_team + risk_rust + risk_cs + risk_shoot
				
				# Build Triggered Flags List
				risk_warnings_list = []
				if risk_foul > 0: risk_warnings_list.append("⚠️ Foul Trouble")
				if risk_size > 0: risk_warnings_list.append("⚠️ Undersized")
				if risk_team > 0: risk_warnings_list.append("⚠️ Empty Stats")
				if risk_rust > 0: risk_warnings_list.append("⚠️ Inactive/Rust")
				if risk_cs > 0: risk_warnings_list.append("⚠️ Unreliable Data")
				if is_fluke: risk_warnings_list.append("⚠️ Shooting Fluke")
				elif is_gem: risk_warnings_list.append("💎 Hidden Gem")
					
				# Dynamic colored risk badges matching Excel logic
				if total_risk_score >= 3.5:
					risk_badge = f"🔴 HIGH RISK ({total_risk_score:.1f})"
				elif total_risk_score >= 1.5:
					risk_badge = f"🟡 CAUTION ({total_risk_score:.1f})"
				else:
					risk_badge = f"🟢 SAFE ({total_risk_score:.1f})"
					
				st.write(f"**Risk Factor:** `{risk_badge}` *(Scale: 0.0 to 7.0)*")
				
				if risk_warnings_list:
					st.write(f"**Triggered Flags:** `{', '.join(risk_warnings_list)}`")
				
				orig_cs = orig_league["CS"]
				dest_cs = dest_league["CS"]
				cs_var_map = {"High": 0.05, "Medium": 0.10, "Low": 0.15, "Very Low": 0.20, "Estimated": 0.25}
				actual_combined_variability = max(cs_var_map.get(orig_cs, 0.20), cs_var_map.get(dest_cs, 0.20))

				st.write(f"**Data Confidence Rating (CS):** `{orig_cs} -> {dest_cs}` *(Dynamic default: ± {actual_combined_variability*100:.0f}%)*")
				
				# --- EXACT EXCEL-BASED BADGES ENGINE ---
				badges = []
				
				# 1. 🎯 Sniper (3P% > 37% AND expected 3PA > 4)
				if proj.get("3P%", 0) > 0.37 and proj.get("proj_3pa", 0) > 4.0:
					badges.append("🎯 Sniper")
					
				# 2. 🧊 Ice Cold FT (FT% > 80%)
				if proj.get("FT%", 0) > 0.80:
					badges.append("🧊 Ice Cold FT")
					
				# 3. 🧱 FT Liability (FT% < 50%)
				if proj.get("FT%", 0) < 0.50:
					badges.append("🧱 FT Liability")
					
				# 4. 🛡️ Rim Protector (Blocks > 0.8)
				if proj.get("BLK", 0) > 0.8:
					badges.append("🛡️ Rim Protector")
					
				# 5. 🧠 Playmaker (Expected Assists > 3.5)
				if proj.get("AS", 0) > 3.5:
					badges.append("🧠 Playmaker")
					
				# 6. 🛑 Ball-Hog (Assists < 1.0 AND Points > 12.0)
				if proj.get("AS", 0) < 1.0 and proj.get("PTS", 0) > 12.0:
					badges.append("🛑 Ball-Hog")
					
				# 7. 💪 Glass Cleaner (Rebounds > 7.0)
				if proj.get("REB", 0) > 7.0:
					badges.append("💪 Glass Cleaner")
					
				# 8. 🐗 Offensive Rebounder (Offensive Rebounds > 2.5)
				if proj.get("OR", 0) > 2.0:
					badges.append("🐗 Offensive Rebounder")
					
				# 9. ✅ Efficient (2P% > 55% AND 3P% > 35%)
				if proj.get("2P%", 0) > 0.55 and proj.get("3P%", 0) > 0.35:
					badges.append("✅ Efficient")
				
				st_badges_str = ", ".join(badges) if badges else "None"
				st.write(f"**Player Badges:** `{st_badges_str}`")
				
				# Star rating
				stars_str = "🌟" * min(5, max(1, int(proj["VAL"] / 4)))
				st.write(f"**Target Impact Rating:** `{stars_str} (Base VAL: {proj['VAL']})`")
				# Persistent Pin to Shortlist button (Uses dynamic keying to support multiple candidates)
				st.markdown("---")
				safe_p_name = player_name.replace(" ", "_").replace("à", "a").replace("á", "a")
				safe_l_name = dest_league_name.replace(" ", "_")
				btn_key = f"pin_to_shortlist_{safe_p_name}_{safe_l_name}"
				
				st.button(
					"📋 Pin to Prospect Shortlist", 
					key=btn_key,
					on_click=add_to_shortlist_callback,
					args=(
						player_name, st.session_state.p_orig_team, orig_league_name, dest_league_name, position, target_role, calculated_age, height, gp, 
						st.session_state.raw_stats, proj, total_risk_score, total_context_multiplier, selected_modifiers, variability,
						origin_role_label, target_role_label, conf_percent, conf_factor_str, risk_badge, dest_cs, st_badges_str, stars_str
					)
				)
				
				# --- NEW: RISK & BADGES SCORING GUIDE ---
				st.markdown("---")
				with st.expander("ℹ️ Risk & Badges Guide", expanded=False):
					st.caption("Understand how individual risk flags and performance badges are calculated based on your Excel-derived matrices:")
					
					st.markdown("**Risk Factor Flags:**")
					st.markdown("- **Foul Trouble (+1.0):** Triggered if personal fouls per 40 mins is > 5.5.")
					st.markdown("- **Undersized (+1.0):** Triggered during an upward tier jump if a Big (C/PF) is < 201cm, or a Guard is < 185cm (applies a x0.90/x0.85 penalty to Rebounds and 2P%).")
					st.markdown("- **Empty Stats (+1.0):** Triggered if previous team finished in the Bottom/Relegation tier.")
					st.markdown("- **Inactive/Rust (+1.0):** Triggered if player played fewer than 12 games in the evaluated season.")
					st.markdown("- **Unreliable Data (+1.5 if Low / +1.5 if Very Low):** Triggered if either origin or destination league confidence (CS) is Low, Very Low, or Estimated.")
					st.markdown("- **Shooting Fluke (+1.5 Fluke / -0.5 Gem):** Fluke triggers if 3P% > 38% but FT% < 70%. Gem triggers if 3P% < 32% but FT% > 82%.")
					
					st.markdown("**Badges Engine:**")
					st.markdown("- 🎯 **Sniper:** Expected 3P% > 37% and expected 3PA > 4.0.")
					st.markdown("- 🧊 **Ice Cold FT:** Expected FT% > 80%.")
					st.markdown("- 🧱 **FT Liability:** Expected FT% < 50%.")
					st.markdown("- 🛡️ **Rim Protector:** Expected Blocks > 0.8.")
					st.markdown("- 🧠 **Playmaker:** Expected Assists > 3.5.")
					st.markdown("- 🛑 **Ball-Hog:** Expected Assists < 1.0 and expected Points > 12.0.")
					st.markdown("- 💪 **Glass Cleaner:** Expected Rebounds > 7.0.")
					st.markdown("- 🐗 **Offensive Rebounder:** Expected Offensive Rebounds > 2.5.")
					st.markdown("- ✅ **Efficient:** Expected 2P% > 55% and expected 3P% > 35%.")

		# Full-width dividing line and scouting tip anchored at the bottom of the tab workspace
		st.markdown("---")
		st.info("💡 Scouting Tip: Projections are statistically independent. A player can perform at their ceiling in one metric (e.g., PTS) while concurrently performing at their baseline or floor in another (e.g., FT%). Each metric is evaluated on its own independent range.")

	# ----------------- TAB 3: CONTEXT RULES & WARNINGS -----------------
	with tab_rules_warnings:
		st.subheader("Auto-Detected Context Rules")
		
		warnings = 0
			
		if is_defensive_glue:
			st.error("Dynamic Rule - Defensive Glue: Player averaged < 0.35 FGA per minute. Role modifier forced to 1.00.")
			warnings += 1
		if continuity_mod > 1.07:
			st.success("Dynamic Rule - Continuity System: Player is staying in the same national system and is under Age 30. Stability modifier of x1.08 applied.")
			warnings += 1
		if is_moving_up and (rebound_height_modifier < 1.0 or derived_height_mod_2p < 1.0):
			st.warning(f"Dynamic Rule - Undersized Penalty: Player is undersized for their position making a tier jump. A x{rebound_height_modifier:.2f} penalty has been applied to Rebounds and 2P%. Flagged (+1.0) in Risk Matrix.")
			warnings += 1
		if derived_height_mod_2p < 1.00:
			st.warning(f"Dynamic Rule - 2P% Height Penalty: Player is undersized for position during tier jump. 2P% penalty applied: x{derived_height_mod_2p:.2f}")
			warnings += 1
		if calculated_age in [22, 23]:
			st.info("Potential Rookie Pro Profile: Age is 22-23. Consider activating the Rookie Pro modifier (-10% penalty).")
			warnings += 1
		elif calculated_age >= 30:
			st.success("Potential Veteran Leader Profile: Age matches 30+. Consider activating Veteran Leader (+5% boost).")
			warnings += 1
			
		raw_ft_rate = st.session_state.raw_stats["FTM"] / st.session_state.raw_stats["FGA"] if st.session_state.raw_stats["FGA"] > 0 else 0
		if (orig_league_name in ["Spain Tercera FEB", "USA TBL"]) and (raw_ft_rate > 0.40) and (dest_league_name == "Spain Segunda FEB"):
			st.warning("Whistle Tax Flagged: High Free Throw Rate in lower league may drop in Segunda FEB. Recommend activating FTR / Whistle Tax.")
			warnings += 1
		if orig_team_quality == "Bottom / Relegation":
			st.warning("Dynamic Rule - Empty Stats: Player comes from a relegated/bottom team. Flagged (+1.0) in Risk Factor matrix.")
			warnings += 1
		elif orig_team_quality == "Top of League / Contender":
			st.success("Dynamic Rule - Winning System: Player comes from a top team. Positively contextualizes efficiency.")
			warnings += 1

		# Pull these variables from raw stats safely for Tab 3
		three_pct = st.session_state.raw_stats.get("3P%", 0)
		ft_pct = st.session_state.raw_stats.get("FT%", 0)

		if three_pct > 0.38 and ft_pct < 0.70:
			st.warning(f"Shooting Fluke Matrix: High 3P% ({three_pct*100:.1f}%) but poor FT% ({ft_pct*100:.1f}%). 3P% is likely unsustainable (+1.5 Risk).")
			warnings += 1
		elif three_pct < 0.32 and ft_pct > 0.82:
			st.success(f"Hidden Gem Found: Low 3P% ({three_pct*100:.1f}%) but elite FT% ({ft_pct*100:.1f}%). 3P% is highly likely to improve (-0.5 Risk).")
			warnings += 1

		# Unreliable data check
		cs_map = {"Estimated": 1, "Very Low": 2, "Low": 3, "Medium": 4, "High": 5}
		if min(cs_map.get(orig_league["CS"], 1), cs_map.get(dest_league["CS"], 1)) <= 2:
			st.error("Unreliable Data Matrix: League confidence score for either origin or destination is 'Very Low' or 'Estimated'. Projections carry high variance (+1.5 Risk).")
			warnings += 1
			
		if gp < 12:
			st.error(f"Inactive / Rust Warning: Player has recorded fewer than 12 Games Played ({gp} GP). High risk of unconditioned integration (+1.0 Risk).")
			warnings += 1
		if calculated_age <= 21:
			st.success(f"Dynamic Rule - Prospect Growth: Player is Age {calculated_age}. A +4% developmental boost has been applied to expected volume metrics.")
			warnings += 1
		elif calculated_age <= 24:
			st.success(f"Dynamic Rule - Young Development: Player is Age {calculated_age}. A +2% developmental boost has been applied to expected volume metrics.")
			warnings += 1
		elif calculated_age >= 36:
			st.error(f"Dynamic Rule - Veteran Decay: Player is Age {calculated_age}. A -8% athletic decay penalty has been applied to expected volume metrics.")
			warnings += 1
		elif calculated_age >= 33:
			st.error(f"Dynamic Rule - Late Decline: Player is Age {calculated_age}. A -5% age-related athletic penalty has been applied to expected volume metrics.")
			warnings += 1
		elif calculated_age >= 30:
			st.warning(f"Dynamic Rule - Early Decline: Player is Age {calculated_age}. A -2% age-related drop-off penalty has been applied to expected volume metrics.")
			warnings += 1
   	
		st.info(f"Volume Safety Variances: [FT Var: {proj['ft_variance']*100:.0f}%]  |  [2P% Var: {proj['twop_variance']*100:.0f}%]  |  [3P% Var: {proj['threep_variance']*100:.0f}%]")
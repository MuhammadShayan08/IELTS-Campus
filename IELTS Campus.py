import streamlit as st
import time
import io
import wave
import hashlib
import secrets
import random
from datetime import datetime, timedelta

# ─── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="IELTS Campus — Practice Tests",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── STORAGE ────────────────────────────────────────────────────────────────────
import shelve, threading

_SHELF_PATH = "/tmp/ielts_campus_v2"
_lock = threading.Lock()

def _load(col):
    try:
        with _lock:
            with shelve.open(_SHELF_PATH) as db:
                return dict(db.get(col, {}))
    except Exception:
        return st.session_state.get(f"_db_{col}", {})

def _save(col, data):
    try:
        with _lock:
            with shelve.open(_SHELF_PATH) as db:
                db[col] = data
    except Exception:
        st.session_state[f"_db_{col}"] = data

try:
    from pymongo import MongoClient
    import certifi

    @st.cache_resource
    def get_db():
        try:
            uri = st.secrets.get("MONGO_URI", "")
            if not uri:
                return None
            c = MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
            c.admin.command('ping')
            return c["ielts_campus_v2"]
        except Exception:
            return None

    def _load(col):
        try:
            db = get_db()
            if db is not None:
                doc = db["kv"].find_one({"_id": col})
                return doc.get("data", {}) if doc else {}
        except Exception:
            pass
        try:
            with _lock:
                with shelve.open(_SHELF_PATH) as s:
                    return dict(s.get(col, {}))
        except Exception:
            return st.session_state.get(f"_db_{col}", {})

    def _save(col, data):
        try:
            db = get_db()
            if db is not None:
                db["kv"].update_one({"_id": col}, {"$set": {"data": data}}, upsert=True)
                return
        except Exception:
            pass
        try:
            with _lock:
                with shelve.open(_SHELF_PATH) as s:
                    s[col] = data
        except Exception:
            st.session_state[f"_db_{col}"] = data

except ImportError:
    pass

USERS_FILE    = "ielts_users_v2"
TOKENS_FILE   = "ielts_tokens_v2"
ACTIVITY_FILE = "ielts_activity_v2"
ADMIN_EMAILS  = {"shayan.code1@gmail.com", "admin@ieltscampus.com"}

# ─── HELPERS ────────────────────────────────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_online_status(ud):
    ls = ud.get("last_seen", ud.get("last_login", ""))
    if not ls:
        return {"dot": "⚫", "color": "#6b7280", "label": "Never", "online": False}
    try:
        diff = datetime.now() - datetime.strptime(ls, "%Y-%m-%d %H:%M:%S")
        mins = int(diff.total_seconds() / 60)
        if diff.total_seconds() <= 60:   return {"dot": "🟢", "color": "#4ade80", "label": "Online now", "online": True}
        elif mins <= 30:                 return {"dot": "🟡", "color": "#fbbf24", "label": f"{mins}m ago", "online": False}
        elif mins <= 120:                return {"dot": "🔴", "color": "#f87171", "label": f"{mins}m ago", "online": False}
        elif diff.days == 0:             return {"dot": "⚫", "color": "#6b7280", "label": f"{int(mins/60)}h ago", "online": False}
        else:                            return {"dot": "⚫", "color": "#6b7280", "label": f"{diff.days}d ago", "online": False}
    except Exception:
        return {"dot": "⚫", "color": "#6b7280", "label": "Unknown", "online": False}

def update_last_seen(email):
    try:
        udb = _load(USERS_FILE)
        if email in udb:
            udb[email]["last_seen"] = now_str()
            _save(USERS_FILE, udb)
    except Exception:
        pass

def log_activity(email, action, detail=""):
    try:
        adb = _load(ACTIVITY_FILE)
        if email not in adb: adb[email] = []
        adb[email].append({"time": now_str(), "action": action, "detail": detail})
        adb[email] = adb[email][-100:]
        _save(ACTIVITY_FILE, adb)
    except Exception:
        pass

def create_token(email):
    import base64, hmac
    secret = "ielts_campus_v2_secret"
    payload = f"{email}|{datetime.now().strftime('%Y-%m-%d')}"
    pb64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret.encode(), pb64.encode(), "sha256").hexdigest()[:16]
    token = f"{pb64}.{sig}"
    tokens = _load(TOKENS_FILE)
    tokens[token] = {"email": email, "expiry": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")}
    _save(TOKENS_FILE, tokens)
    return token

def validate_token(token):
    if not token: return None
    import base64, hmac
    secret = "ielts_campus_v2_secret"
    try:
        pb64, sig = token.split(".")
        exp = hmac.new(secret.encode(), pb64.encode(), "sha256").hexdigest()[:16]
        if hmac.compare_digest(sig, exp):
            payload = base64.urlsafe_b64decode(pb64.encode()).decode()
            email, date_str = payload.rsplit("|", 1)
            if (datetime.now() - datetime.strptime(date_str, "%Y-%m-%d")).days <= 30:
                return email
    except Exception:
        pass
    tokens = _load(TOKENS_FILE)
    entry = tokens.get(token)
    if entry and entry.get("expiry", "0000") >= datetime.now().strftime("%Y-%m-%d"):
        return entry.get("email")
    return None

def delete_token(token):
    tokens = _load(TOKENS_FILE)
    tokens.pop(token, None)
    _save(TOKENS_FILE, tokens)

# ─── SESSION STATE ───────────────────────────────────────────────────────────────
if "theme" not in st.session_state:       st.session_state.theme = "dark"
if "auth_mode" not in st.session_state:   st.session_state.auth_mode = "signin"
if "scores" not in st.session_state:      st.session_state.scores = {}
if "show_admin" not in st.session_state:  st.session_state.show_admin = False

# ── Auto-login ──
if "sidebar_open" not in st.session_state:
    st.session_state.sidebar_open = True
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.current_user  = None
    st.session_state.login_token   = None
    try:
        tok = st.query_params.get("token", None)
        if tok:
            em = validate_token(tok)
            if em:
                udb = _load(USERS_FILE)
                ud  = udb.get(em, {})
                if not ud.get("banned") and not ud.get("suspended"):
                    name = ud.get("name", em.split("@")[0].replace(".", " ").title())
                    st.session_state.authenticated = True
                    st.session_state.current_user  = {"name": name, "email": em}
                    st.session_state.login_token   = tok
    except Exception:
        pass

T = st.session_state.theme

# ─── THEME PALETTES ─────────────────────────────────────────────────────────────
if T == "dark":
    BG = "#000000"; BG2 = "#0d0d0d"; BG3 = "#141414"; BG4 = "#1c1c1c"
    BORDER = "#222222"; TEXT1 = "#f9fafb"; TEXT2 = "#9ca3af"; TEXT3 = "#6b7280"
    ACCENT = "#E8380D"; ACCENT2 = "#ff6b3d"; ACCENT_DIM = "rgba(232,56,13,0.15)"
    ACCENT_BORDER = "rgba(232,56,13,0.40)"
    CARD_BG = "#0d0d0d"; HDR_BG = "#000000"
    SIDEBAR_BG = "linear-gradient(180deg,#000000 0%,#0d0d0d 100%)"
    BTN_BG = "linear-gradient(135deg,#c72e09,#E8380D)"
    BTN_GLOW = "rgba(232,56,13,0.45)"
    HERO_GRAD = "linear-gradient(135deg,#E8380D 0%,#ff6b3d 50%,#fbbf24 100%)"
    TAB_SEL = "linear-gradient(135deg,#c72e09,#E8380D)"
    SCORE_BG = "#0d1117"
    CHART_TEMPLATE = "plotly_dark"
    GLOW_DIV = "linear-gradient(90deg,transparent,#E8380D,#ff6b3d,transparent)"
    MOD_BG = "#0d0d0d"
    INFO_BG = "rgba(232,56,13,0.06)"
    BAND_COLORS = ["#E8380D","#ff6b3d","#fbbf24","#4ade80","#60a5fa","#c084fc"]
    PILL_A = "rgba(232,56,13,0.15)"; PILL_AC = "#E8380D"
    PILL_B = "rgba(255,107,61,0.12)"; PILL_BC = "#ff6b3d"
    PILL_C = "rgba(251,191,36,0.12)"; PILL_CC = "#fbbf24"
    SB_SHADOW = "4px 0 24px rgba(0,0,0,0.8)"
else:
    BG = "#fafafa"; BG2 = "#ffffff"; BG3 = "#f5f0ed"; BG4 = "#ede8e3"
    BORDER = "#e0d8d2"; TEXT1 = "#1a0a00"; TEXT2 = "#6b4c3b"; TEXT3 = "#9b7a6a"
    ACCENT = "#E8380D"; ACCENT2 = "#c72e09"; ACCENT_DIM = "rgba(232,56,13,0.10)"
    ACCENT_BORDER = "rgba(232,56,13,0.35)"
    CARD_BG = "#ffffff"; HDR_BG = "#ffffff"
    SIDEBAR_BG = "#f5f0ed"
    BTN_BG = "linear-gradient(135deg,#c72e09,#E8380D)"
    BTN_GLOW = "rgba(232,56,13,0.35)"
    HERO_GRAD = "linear-gradient(135deg,#E8380D 0%,#c72e09 50%,#8b1a00 100%)"
    TAB_SEL = "linear-gradient(135deg,#c72e09,#E8380D)"
    SCORE_BG = "#1a0a00"
    CHART_TEMPLATE = "plotly_white"
    GLOW_DIV = "linear-gradient(90deg,transparent,#E8380D88,transparent)"
    MOD_BG = "#1a0a00"
    INFO_BG = "rgba(232,56,13,0.06)"
    BAND_COLORS = ["#E8380D","#c72e09","#8b1a00","#6b4c3b","#9b7a6a","#bca99b"]
    PILL_A = "rgba(232,56,13,0.12)"; PILL_AC = "#E8380D"
    PILL_B = "rgba(199,46,9,0.10)"; PILL_BC = "#c72e09"
    PILL_C = "rgba(139,26,0,0.10)"; PILL_CC = "#8b1a00"
    SB_SHADOW = "4px 0 16px rgba(0,0,0,0.10)"

# ─── GLOBAL CSS ──────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800;900&family=DM+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

*,*::before,*::after {{
    transition:background 0.5s ease,background-color 0.5s ease,color 0.4s ease,border-color 0.4s ease !important;
    box-sizing:border-box !important;
}}
body, p, span, div, input, textarea, select, button, label, td, th, li {{
    font-family:'DM Sans',-apple-system,sans-serif !important;
}}
/* ── HIDE ALL STREAMLIT CHROME ── */
#MainMenu{{visibility:hidden;}}footer{{visibility:hidden;}}
header[data-testid="stHeader"]{{display:none !important;}}
[data-testid="stToolbar"]{{display:none !important;}}
[data-testid="stDecoration"]{{display:none !important;}}
[data-testid="stStatusWidget"]{{display:none !important;}}
[data-testid="collapsedControl"]{{display:none !important;}}
.stAppToolbar{{display:none !important;}}
div[class*="viewerBadge"]{{display:none !important;}}

html,body {{ background:{BG} !important; color:{TEXT1} !important; }}
.main,.block-container,[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"] {{ background:{BG} !important; }}
.block-container {{ padding-top:1rem !important; max-width:1380px; }}



/* ── SIDEBAR ── */
{"" if st.session_state.get("sidebar_open", True) else "section[data-testid=\"stSidebar\"] { display:none !important; }"}
section[data-testid="stSidebar"]{{background:{"linear-gradient(180deg,#0a0000 0%,#100500 100%)" if T=="dark" else "#fdf8f5"} !important;border-right:{"1px solid #1c1c1c" if T=="dark" else "2px solid #e8d5c8"} !important;box-shadow:{"4px 0 20px rgba(0,0,0,0.6)" if T=="dark" else "4px 0 16px rgba(0,0,0,0.08)"} !important;min-height:100vh !important;}}
section[data-testid="stSidebar"]>div{{background:{"transparent" if T=="dark" else "#fdf8f5"} !important;}}
section[data-testid="stSidebar"] *{{color:{TEXT1} !important;}}
section[data-testid="stSidebar"] hr{{border-color:{BORDER} !important;}}
section[data-testid="stSidebar"] .stButton>button{{background:{BTN_BG} !important;color:#ffffff !important;box-shadow:0 4px 14px {BTN_GLOW} !important;}}

/* ── MOBILE RESPONSIVE ── */
@media (max-width: 768px) {{
    .vibe-header {{ padding:1.5rem 1.2rem !important; }}
    .vibe-header h1 {{ font-size:1.8rem !important; }}
    .vibe-header::after {{ display:none !important; }}
    .module-grid {{ grid-template-columns:1fr !important; }}
    .stat-grid {{ grid-template-columns:repeat(2,1fr) !important; }}
    .header-bar {{ flex-wrap:wrap !important; gap:.5rem !important; }}
    .band-grid {{ grid-template-columns:1fr !important; }}
}}

/* ── TYPOGRAPHY ── */
h1,h2,h3,h4,h5 {{ font-family:'Playfair Display',serif !important; color:{TEXT1} !important; }}

/* ── VIBE HEADER ── */
.vibe-header {{
    position:relative; padding:2.5rem 3rem; border-radius:20px;
    margin-bottom:1.5rem; overflow:hidden;
    background:{"linear-gradient(135deg,#000000 0%,#0d0500 60%,#000000 100%)" if T=="dark" else "linear-gradient(135deg,#1a0a00 0%,#2d1200 60%,#1a0a00 100%)"};
    border:1px solid {ACCENT_BORDER};
    box-shadow:0 0 48px {"rgba(232,56,13,0.20)" if T=="dark" else "rgba(232,56,13,0.30)"};
}}
.vibe-header::before {{
    content:''; position:absolute; inset:0;
    background:radial-gradient(ellipse 70% 70% at 10% 50%, {"rgba(232,56,13,0.18)" if T=="dark" else "rgba(232,56,13,0.22)"} 0%, transparent 60%);
    animation:pulseGlow 5s ease-in-out infinite alternate;
}}
.vibe-header::after {{
    content:'✈️'; position:absolute; right:3rem; top:50%;
    transform:translateY(-50%); font-size:8rem; opacity:0.06; line-height:1;
}}
@keyframes pulseGlow {{ from{{opacity:.5}} to{{opacity:1}} }}
.vibe-header h1 {{
    font-family:'Playfair Display',serif !important;
    font-size:2.8rem; font-weight:900; margin:0; letter-spacing:-.02em; line-height:1.1;
    background:{HERO_GRAD}; -webkit-background-clip:text;
    -webkit-text-fill-color:transparent; background-clip:text;
}}
.vibe-header .tagline {{ font-size:.95rem; margin-top:.5rem; color:{"#a7633a" if T=="dark" else "#c4845a"}; font-weight:400; }}
.vibe-header .pill-row {{ display:flex; gap:.5rem; margin-top:1rem; flex-wrap:wrap; }}
.pill {{
    display:inline-flex; align-items:center; gap:.3rem; padding:.25rem .8rem;
    border-radius:999px; font-size:.68rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase;
}}
.pill-a {{ background:{PILL_A}; color:{PILL_AC}; border:1px solid {ACCENT_BORDER}; }}
.pill-b {{ background:{PILL_B}; color:{PILL_BC}; border:1px solid rgba(255,107,61,0.30); }}
.pill-c {{ background:{PILL_C}; color:{PILL_CC}; border:1px solid rgba(251,191,36,0.25); }}

/* ── STAT GRID ── */
.stat-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin-bottom:1.5rem; }}
.stat-card {{
    background:{CARD_BG}; border:1px solid {BORDER}; border-radius:16px;
    padding:1.25rem 1.5rem; position:relative; overflow:hidden; cursor:default;
}}
.stat-card:hover {{ border-color:{ACCENT}; transform:translateY(-3px); box-shadow:0 8px 28px {BTN_GLOW}; }}
.stat-card .bar {{ position:absolute; top:0; left:0; right:0; height:3px;
    background:{BTN_BG}; transform:scaleX(0); transform-origin:left; }}
.stat-card:hover .bar {{ transform:scaleX(1); }}
.stat-card .label {{ font-size:.65rem; font-weight:700; text-transform:uppercase;
    letter-spacing:.1em; color:{TEXT3}; margin-bottom:.4rem; }}
.stat-card .value {{ font-size:2.2rem; font-weight:900; color:{ACCENT}; line-height:1; font-family:'Playfair Display',serif !important; }}
.stat-card .sub {{ font-size:.72rem; color:{TEXT3}; margin-top:.25rem; }}

/* ── SECTION HEAD ── */
.section-head {{ display:flex; align-items:center; gap:.75rem; margin:1.75rem 0 1rem; }}
.section-head .icon-wrap {{
    width:36px; height:36px; border-radius:10px; display:flex; align-items:center;
    justify-content:center; font-size:1rem;
    background:{ACCENT_DIM}; border:1px solid {ACCENT_BORDER};
}}
.section-head h3 {{ margin:0; font-size:1.05rem; font-weight:700; color:{TEXT1}; font-family:'DM Sans',sans-serif !important; }}

/* ── FEATURE CARDS ── */
.feature-card {{
    background:{CARD_BG}; border:1px solid {BORDER}; border-radius:18px;
    padding:1.75rem; position:relative; overflow:hidden;
}}
.feature-card:hover {{ border-color:{ACCENT}; transform:translateY(-3px); box-shadow:0 8px 28px {BTN_GLOW}; }}
.feature-card .fc-icon {{ font-size:2.2rem; margin-bottom:.75rem; }}
.feature-card h3 {{ margin:0 0 .5rem; font-size:1rem; font-weight:700; color:{TEXT1}; font-family:'DM Sans' !important; }}
.feature-card p {{ margin:0; font-size:.875rem; color:{TEXT2}; line-height:1.6; }}

/* ── MODULE CARDS ── */
.module-card {{
    background:{MOD_BG}; color:white; border-radius:16px;
    padding:1.75rem 1.25rem; text-align:center;
    border-bottom:4px solid {ACCENT};
    border:1px solid {"#1c1c1c" if T=="dark" else "#2d1200"};
    border-bottom:4px solid {ACCENT};
    transition:all 0.3s ease !important;
}}
.module-card:hover {{ transform:translateY(-4px); box-shadow:0 12px 32px {BTN_GLOW}; }}

/* ── SECTION CARDS ── */
.section-card {{
    background:{CARD_BG}; border-radius:14px;
    padding:1.5rem 2rem; border-left:5px solid {ACCENT};
    border:1px solid {BORDER}; border-left:5px solid {ACCENT};
    margin-bottom:1.5rem;
    box-shadow:0 2px 16px {"rgba(0,0,0,0.4)" if T=="dark" else "rgba(0,0,0,0.06)"};
}}
.section-title {{
    font-family:'Playfair Display',serif !important;
    font-size:1.4rem; font-weight:800; color:{TEXT1}; margin-bottom:.25rem;
}}
.section-desc {{ color:{TEXT3}; font-size:.875rem; line-height:1.6; }}

/* ── SCORE BOX ── */
.score-box {{
    background:{SCORE_BG}; color:white; border-radius:16px;
    padding:2rem; text-align:center; font-size:3rem; font-weight:900;
    font-family:'Playfair Display',serif !important;
    margin:1rem 0; border-bottom:4px solid {ACCENT};
}}
.score-label {{ font-size:.85rem; color:#ff9070; margin-top:.5rem; font-weight:600; }}

/* ── TOPIC CARD ── */
.topic-card {{
    background:{INFO_BG}; border:1.5px solid {ACCENT_BORDER};
    border-radius:14px; padding:1.5rem 1.75rem; margin-bottom:1.5rem;
}}
.topic-card h4 {{
    color:{ACCENT}; font-size:.7rem; font-weight:800; text-transform:uppercase;
    letter-spacing:1px; margin:0 0 .5rem; font-family:'DM Sans' !important;
}}
.topic-card .cue {{
    font-family:'Playfair Display',serif !important;
    color:{TEXT1}; font-size:1.15rem; font-weight:700; margin-bottom:.75rem;
}}
.topic-card ul {{ color:{TEXT2}; padding-left:1.2rem; font-size:.9rem; line-height:1.85; margin:0; }}

/* ── TIMER BOX ── */
.timer-box {{
    background:{INFO_BG}; border:1px solid {ACCENT_BORDER}; border-radius:8px;
    padding:.5rem 1rem; font-weight:700; color:{ACCENT};
    display:inline-block; margin-bottom:1rem; font-size:.82rem;
}}

/* ── BUTTONS ── */
.stButton>button {{
    background:{BTN_BG} !important; color:#fff !important; border:none !important;
    padding:.75rem 1.75rem !important; font-weight:700 !important;
    font-size:.9rem !important; border-radius:10px !important;
    box-shadow:0 4px 16px {BTN_GLOW} !important; letter-spacing:.02em !important;
}}
.stButton>button:hover {{
    transform:translateY(-2px) !important;
    box-shadow:0 8px 28px {BTN_GLOW} !important;
    filter:brightness(1.08) !important;
}}

/* ── TABS ── */
.stTabs [data-baseweb="tab-list"] {{
    background:{CARD_BG} !important; border:1px solid {BORDER} !important;
    border-radius:14px !important; padding:5px !important; gap:3px !important;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius:10px !important; font-weight:600 !important;
    border:none !important; color:{TEXT2} !important;
    background:transparent !important; padding:.6rem 1.3rem !important;
    font-size:.88rem !important;
}}
.stTabs [data-baseweb="tab"]:hover {{ background:{BG3} !important; color:{TEXT1} !important; }}
.stTabs [aria-selected="true"] {{
    background:{TAB_SEL} !important; color:#fff !important;
    box-shadow:0 4px 14px {BTN_GLOW} !important;
}}

/* ── INPUTS ── */
[data-testid="stTextInput"] input {{
    border:1.5px solid {BORDER} !important; border-radius:10px !important;
    font-size:.9rem !important; padding:.65rem .9rem !important;
    background:{BG3} !important; color:{TEXT1} !important;
}}
[data-testid="stTextInput"] input:focus {{
    border-color:{ACCENT} !important;
    box-shadow:0 0 0 3px rgba(232,56,13,0.12) !important;
}}
textarea {{
    border:1.5px solid {BORDER} !important; border-radius:10px !important;
    background:{BG3} !important; color:{TEXT1} !important;
}}
textarea:focus {{
    border-color:{ACCENT} !important;
    box-shadow:0 0 0 3px rgba(232,56,13,0.12) !important;
}}
.stSelectbox>div>div {{
    background:{BG3} !important; border:1.5px solid {BORDER} !important;
    border-radius:10px !important; color:{TEXT1} !important;
}}

/* ── METRICS ── */
[data-testid="metric-container"] {{
    background:{INFO_BG}; border:1px solid {ACCENT_BORDER};
    border-radius:10px; padding:.9rem 1rem;
}}
[data-testid="stMetricValue"] {{ color:{ACCENT} !important; font-weight:900 !important; font-family:'Playfair Display',serif !important; }}
[data-testid="stMetricLabel"] {{ color:{TEXT3} !important; font-size:.72rem !important; font-weight:700 !important; text-transform:uppercase; }}

/* ── RADIO ── */
.stRadio label {{ color:{TEXT2} !important; font-size:.9rem !important; }}

/* ── EXPANDER ── */
[data-testid="stExpander"] {{
    background:{CARD_BG} !important; border:1px solid {BORDER} !important;
    border-radius:12px !important;
}}
[data-testid="stExpander"] p,
[data-testid="stExpander"] div {{ color:{TEXT2} !important; line-height:1.75 !important; }}
[data-testid="stExpander"] summary {{ font-weight:700 !important; color:{TEXT1} !important; }}

/* ── ALERTS ── */
.stAlert {{ background:{INFO_BG} !important; border-radius:10px !important; border-left:4px solid {ACCENT} !important; }}

/* ── SIDEBAR NAV BUTTONS ── */
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] .stButton>button {{
    background:{"rgba(255,255,255,0.05)" if T=="dark" else "rgba(26,10,0,0.05)"} !important;
    color:{TEXT2} !important;
    border:1px solid {BORDER} !important;
    border-radius:8px !important; font-size:.82rem !important;
    font-weight:600 !important; padding:.55rem .9rem !important;
    box-shadow:none !important; text-align:left !important;
    justify-content:flex-start !important;
}}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] .stButton>button:hover {{
    background:{ACCENT_DIM} !important; color:{ACCENT} !important;
    border-color:{ACCENT_BORDER} !important; transform:none !important;
}}

/* ── AUTH CARD ── */
.auth-card {{
    background:{CARD_BG}; border-radius:20px; padding:2.5rem 2rem;
    box-shadow:0 4px 32px {"rgba(0,0,0,0.6)" if T=="dark" else "rgba(0,0,0,0.10)"};
    border:1px solid {BORDER}; max-width:440px; margin:0 auto;
}}

/* ── ADMIN ── */
.admin-badge {{
    display:inline-flex; align-items:center; gap:5px;
    background:{ACCENT_DIM}; color:{ACCENT};
    border:1px solid {ACCENT_BORDER}; border-radius:999px;
    padding:.2rem .75rem; font-size:.68rem; font-weight:800;
    text-transform:uppercase; letter-spacing:.6px;
}}

/* ── TROPHY BANNER ── */
.trophy-banner {{
    border-radius:20px; padding:1.75rem 2rem;
    background:{"linear-gradient(135deg,rgba(232,56,13,0.10),rgba(255,107,61,0.05))" if T=="dark" else "linear-gradient(135deg,#1a0a00,#2d1200)"};
    border:1px solid {ACCENT_BORDER};
    display:flex; align-items:center; gap:1.25rem; margin-bottom:1.25rem;
    position:relative; overflow:hidden;
}}
.trophy-icon {{ font-size:3rem; flex-shrink:0; }}
.trophy-text h2 {{ margin:0; font-size:1.5rem; font-weight:900; color:{"#f9fafb" if T=="dark" else "#f5f0ed"}; }}
.trophy-text p {{ margin:.2rem 0 0; font-size:.85rem; color:{"#9ca3af" if T=="dark" else "#c4845a"}; }}
.trophy-score {{
    margin-left:auto; text-align:right; flex-shrink:0;
    padding:.65rem 1.25rem; background:{ACCENT_DIM};
    border-radius:12px; border:1px solid {ACCENT_BORDER};
}}
.trophy-score .ts-label {{ font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.08em; color:{TEXT3}; }}
.trophy-score .ts-value {{ font-size:2rem; font-weight:900; color:{ACCENT}; font-family:'Playfair Display',serif !important; }}

/* ── PLAN BADGE ── */
.plan-badge {{
    display:inline-flex; align-items:center; gap:.3rem;
    padding:.2rem .75rem; border-radius:999px;
    font-size:.7rem; font-weight:800; letter-spacing:.05em; text-transform:uppercase;
}}
.plan-badge.free {{ background:rgba(107,114,128,0.15); color:#9ca3af; border:1px solid rgba(107,114,128,0.3); }}
.plan-badge.admin {{ background:{ACCENT_DIM}; color:{ACCENT}; border:1px solid {ACCENT_BORDER}; }}

/* ── GLOW DIVIDER ── */
.glow-divider {{ height:1px; background:{GLOW_DIV}; margin:1.25rem 0; opacity:.5; }}

/* ── SCROLLBAR ── */
::-webkit-scrollbar {{ width:5px; height:5px; }}
::-webkit-scrollbar-track {{ background:{BG}; }}
::-webkit-scrollbar-thumb {{ background:{BG4}; border-radius:3px; }}
::-webkit-scrollbar-thumb:hover {{ background:{ACCENT}; }}

/* ── HEADER PILL BTNS ── */
div[data-testid="stHorizontalBlock"]>div:nth-child(2) .stButton>button,
div[data-testid="stHorizontalBlock"]>div:nth-child(3) .stButton>button {{
    background:{"rgba(255,255,255,0.06)" if T=="dark" else "rgba(26,10,0,0.05)"} !important;
    color:{TEXT1} !important; border:1px solid {BORDER} !important;
    border-radius:20px !important; font-size:.75rem !important;
    font-weight:600 !important; padding:0 12px !important;
    height:30px !important; min-height:30px !important; white-space:nowrap !important;
    box-shadow:none !important;
}}
div[data-testid="stHorizontalBlock"]>div:nth-child(2) .stButton>button:hover,
div[data-testid="stHorizontalBlock"]>div:nth-child(3) .stButton>button:hover {{
    background:{ACCENT} !important; color:white !important;
    border-color:{ACCENT} !important; transform:none !important;
}}

@keyframes slideUp {{ from{{opacity:0;transform:translateY(14px)}} to{{opacity:1;transform:none}} }}
.slide-up {{ animation:slideUp .45s ease-out both; }}
</style>
""", unsafe_allow_html=True)


# ─── AUTH PAGE ────────────────────────────────────────────────────────────────────
if not st.session_state.authenticated:
    st.markdown(f"""<style>
    section[data-testid="stSidebar"] {{ display:none !important; }}
    [data-testid="collapsedControl"] {{ display:none !important; }}
    .block-container {{ padding-top:2rem !important; max-width:520px !important; margin:0 auto !important; }}
    [data-testid="stAppViewBlockContainer"] {{ display:flex; align-items:center; justify-content:center; min-height:100vh; }}
    </style>""", unsafe_allow_html=True)

    st.markdown(f"""
    <div style='text-align:center;padding:2rem 0 1.5rem;'>
        <div style='font-size:3rem;margin-bottom:.4rem;'>✈️</div>
        <h1 style='font-family:Playfair Display,serif !important;font-size:2.2rem;font-weight:900;
                   margin:0 0 .3rem;background:{HERO_GRAD};-webkit-background-clip:text;
                   -webkit-text-fill-color:transparent;background-clip:text;'>IELTS Campus</h1>
        <p style='color:{TEXT3};font-size:.9rem;margin:0;'>Travel Through Language — Free Practice Tests</p>
        <div style='height:3px;width:60px;background:{BTN_BG};border-radius:2px;margin:1rem auto 0;'></div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔑 Sign In", key="tab_si", use_container_width=True):
            st.session_state.auth_mode = "signin"; st.rerun()
    with c2:
        if st.button("✨ Sign Up", key="tab_su", use_container_width=True):
            st.session_state.auth_mode = "signup"; st.rerun()

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)

    if st.session_state.auth_mode == "signup":
        st.markdown(f"<h3 style='color:{TEXT1};font-size:1.3rem;'>📝 Create Your Account</h3>", unsafe_allow_html=True)
        name  = st.text_input("Full Name", placeholder="Your full name", key="su_name")
        email = st.text_input("Email Address", placeholder="your@email.com", key="su_email")
        pw    = st.text_input("Password", type="password", placeholder="At least 6 characters", key="su_pw")
        pw2   = st.text_input("Confirm Password", type="password", placeholder="Repeat password", key="su_pw2")

        if st.button("🚀 Create Account", use_container_width=True, key="do_signup"):
            _n  = st.session_state.get("su_name","").strip()
            _e  = st.session_state.get("su_email","").strip().lower()
            _p  = st.session_state.get("su_pw","")
            _p2 = st.session_state.get("su_pw2","")
            if not _n or not _e or not _p:
                st.error("❌ Please fill in all fields.")
            elif "@" not in _e:
                st.error("❌ Enter a valid email address.")
            elif _p != _p2:
                st.error("❌ Passwords do not match.")
            elif len(_p) < 6:
                st.error("❌ Password must be at least 6 characters.")
            else:
                users = _load(USERS_FILE)
                if _e in users:
                    st.error("❌ An account with this email already exists.")
                else:
                    users[_e] = {
                        "name": _n, "email": _e,
                        "password_hash": hash_pw(_p),
                        "signup_date": now_str(), "last_login": now_str(),
                        "login_count": 1, "banned": False, "suspended": False,
                    }
                    _save(USERS_FILE, users)
                    token = create_token(_e)
                    st.session_state.authenticated = True
                    st.session_state.current_user  = {"name": _n, "email": _e}
                    st.session_state.login_token   = token
                    try: st.query_params["token"] = token
                    except: pass
                    log_activity(_e, "signup", "Account created")
                    st.success(f"✅ Welcome, {_n}! Let's ace IELTS 🎓")
                    time.sleep(0.8); st.rerun()
    else:
        st.markdown(f"<h3 style='color:{TEXT1};font-size:1.3rem;'>👋 Welcome Back!</h3>", unsafe_allow_html=True)
        email = st.text_input("Email Address", placeholder="your@email.com", key="si_email")
        pw    = st.text_input("Password", type="password", placeholder="Your password", key="si_pw")

        if st.button("⚡ Sign In", use_container_width=True, key="do_signin"):
            _e = st.session_state.get("si_email","").strip().lower()
            _p = st.session_state.get("si_pw","")
            if not _e or not _p:
                st.error("❌ Please enter your email and password.")
            else:
                users = _load(USERS_FILE)
                if _e not in users:
                    st.error("❌ No account found. Please Sign Up first.")
                elif users[_e].get("password_hash") != hash_pw(_p):
                    st.error("❌ Incorrect password.")
                elif users[_e].get("banned"):
                    st.error(f"🚫 Account banned: {users[_e].get('ban_reason','Policy violation')}")
                elif users[_e].get("suspended"):
                    st.error("⏸️ Account suspended. Contact support.")
                else:
                    ud = users[_e]
                    users[_e]["last_login"]  = now_str()
                    users[_e]["login_count"] = ud.get("login_count",0) + 1
                    _save(USERS_FILE, users)
                    token = create_token(_e)
                    st.session_state.authenticated = True
                    st.session_state.current_user  = {"name": ud["name"], "email": _e}
                    st.session_state.login_token   = token
                    try: st.query_params["token"] = token
                    except: pass
                    log_activity(_e, "signin", f"Login #{users[_e]['login_count']}")
                    st.success(f"✅ Welcome back, {ud['name']}! 🎓")
                    time.sleep(0.8); st.rerun()

    st.markdown(f"""
    <div style='text-align:center;margin-top:1.5rem;color:{TEXT3};font-size:.78rem;'>
        🔒 Stored securely &nbsp;·&nbsp;
        <a href='https://ieltscampus.com' target='_blank' style='color:{ACCENT};font-weight:700;'>ieltscampus.com</a>
    </div>""", unsafe_allow_html=True)
    st.stop()

# ─── CURRENT USER ────────────────────────────────────────────────────────────────
_cu    = st.session_state.current_user or {}
uemail = _cu.get("email","")
uname  = _cu.get("name","User")
is_admin = uemail in ADMIN_EMAILS

if uemail: update_last_seen(uemail)

# ── Freeze check ──
_udb_chk = _load(USERS_FILE)
_ud_chk  = _udb_chk.get(uemail, {})
if not is_admin and _ud_chk.get("frozen"):
    st.markdown(f"""
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                min-height:80vh;text-align:center;padding:3rem">
      <div style="font-size:5rem;margin-bottom:1rem">🧊</div>
      <div style="font-size:1.8rem;font-weight:900;color:#60a5fa;margin-bottom:.5rem">Account Frozen</div>
      <div style="font-size:.95rem;color:{TEXT3};max-width:380px;line-height:1.8;">
        Your account has been temporarily frozen.<br>
        <b style="color:{TEXT1}">Reason:</b> {_ud_chk.get('frozen_reason') or 'Administrative action'}<br>
        Contact <a href="mailto:support@ieltscampus.com" style="color:{ACCENT};">support@ieltscampus.com</a>
      </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

is_readonly = not is_admin and _ud_chk.get("readonly", False)
if is_readonly:
    st.warning("👁️ **Read-Only Mode** — You can view content but cannot submit tests.")

# ─── DATA ─────────────────────────────────────────────────────────────────────────
READING_PASSAGES = [
    {
        "title": "The Impact of Remote Work",
        "passage": """The COVID-19 pandemic fundamentally transformed the global workforce, accelerating the adoption of remote work on an unprecedented scale. Millions of employees who had never worked from home found themselves suddenly navigating the challenges and opportunities of a home office environment.

Research conducted by Stanford University found that remote workers showed a 13% performance increase compared to their office counterparts. This improvement was attributed to fewer breaks, fewer sick days, and a quieter working environment. However, the same study noted that social isolation and lack of collaboration remained significant challenges for many remote employees.

Companies like Twitter and Shopify announced permanent remote work policies, while others like Apple and Goldman Sachs pushed for employees to return to the office. This divergence highlighted the ongoing debate about productivity, company culture, and employee wellbeing in the post-pandemic era.

The environmental impact of reduced commuting has also been notable. A study estimated that remote work could reduce carbon emissions by 54 million tons per year if those with compatible jobs worked from home half the time. Urban planners are now reconsidering city infrastructure as daily commuting patterns shift dramatically.""",
        "questions": [
            {"q": "By what percentage did remote workers' performance increase according to Stanford?", "options": ["10%","13%","15%","20%"], "answer": "13%", "explanation": "The passage states 'remote workers showed a 13% performance increase'."},
            {"q": "Which company announced a permanent remote work policy?", "options": ["Apple","Goldman Sachs","Shopify","Stanford University"], "answer": "Shopify", "explanation": "'Twitter and Shopify announced permanent remote work policies'."},
            {"q": "What was NOT a reason for improved remote work performance?", "options": ["Fewer breaks","Fewer sick days","Better technology","Quieter environment"], "answer": "Better technology", "explanation": "Technology is not mentioned; breaks, sick days, and quiet environment are."},
            {"q": "What environmental benefit was highlighted?", "options": ["Reduced plastic waste","Lower energy consumption","Reduced carbon emissions","Less water usage"], "answer": "Reduced carbon emissions", "explanation": "'could reduce carbon emissions by 54 million tons per year'."},
        ]
    },
    {
        "title": "Coral Reef Ecosystems",
        "passage": """Coral reefs, often referred to as the 'rainforests of the sea,' cover less than 1% of the ocean floor yet support approximately 25% of all marine species. These complex ecosystems have existed for millions of years, providing shelter, food, and breeding grounds for countless organisms.

The health of coral reefs is threatened by multiple factors. Rising ocean temperatures caused by climate change trigger coral bleaching — a stress response where corals expel the algae living in their tissues, turning white and becoming vulnerable to disease. When water temperatures return to normal quickly, corals can recover, but prolonged bleaching events cause permanent damage.

Ocean acidification presents another serious challenge. As oceans absorb increasing amounts of carbon dioxide, the water becomes more acidic, making it harder for corals and other marine organisms to build their calcium carbonate skeletons. Scientists estimate that if current trends continue, coral reefs could become structurally compromised by 2050.

Conservation efforts include the establishment of marine protected areas, coral restoration programs where scientists grow and transplant coral fragments, and community-based management initiatives. Some researchers are also exploring the possibility of developing heat-resistant coral varieties through selective breeding or genetic modification.""",
        "questions": [
            {"q": "What percentage of marine species do coral reefs support?", "options": ["10%","15%","25%","50%"], "answer": "25%", "explanation": "'support approximately 25% of all marine species'."},
            {"q": "What causes coral bleaching?", "options": ["Ocean acidification","Rising ocean temperatures","Pollution","Overfishing"], "answer": "Rising ocean temperatures", "explanation": "'Rising ocean temperatures caused by climate change trigger coral bleaching'."},
            {"q": "By what year could reefs become structurally compromised?", "options": ["2030","2040","2050","2060"], "answer": "2050", "explanation": "'coral reefs could become structurally compromised by 2050'."},
            {"q": "Which conservation method grows and transplants coral fragments?", "options": ["Marine protected areas","Coral restoration programs","Community management","Genetic modification"], "answer": "Coral restoration programs", "explanation": "'coral restoration programs where scientists grow and transplant coral fragments'."},
        ]
    },
    {
        "title": "The Psychology of Colour",
        "passage": """Colour psychology is a branch of behavioural psychology that studies how different hues affect human mood, perception, and behaviour. While individual responses to colour can vary based on culture, personal experience, and context, researchers have identified several consistent patterns across populations.

Red is widely associated with urgency, passion, and danger. Studies show that viewing red increases heart rate and can stimulate appetite, which is why many fast-food chains incorporate it prominently in their branding. Blue, by contrast, is linked to calm, trust, and productivity. Many financial institutions and technology companies use blue as their primary colour to evoke reliability.

Yellow is the most visible colour in daylight and is strongly associated with optimism and mental clarity. However, excessive use of yellow can cause visual fatigue and even anxiety in sensitive individuals. Green is universally connected to nature and health, and research suggests it can reduce stress and improve concentration in workplace settings.

The cultural dimension of colour perception adds complexity to the field. In Western cultures, white typically represents purity and is used in wedding ceremonies, while in some East Asian cultures, white is the colour of mourning. Similarly, red symbolises luck and prosperity in China, whereas it represents danger or prohibition in many Western contexts.""",
        "questions": [
            {"q": "Which colour is linked to calm and productivity?", "options": ["Red","Yellow","Blue","Green"], "answer": "Blue", "explanation": "'Blue is linked to calm, trust, and productivity'."},
            {"q": "What negative effect can excessive yellow cause?", "options": ["Increased appetite","Lower heart rate","Visual fatigue","Improved concentration"], "answer": "Visual fatigue", "explanation": "'excessive use of yellow can cause visual fatigue'."},
            {"q": "In which culture does white represent mourning?", "options": ["Western cultures","Some East Asian cultures","Latin American cultures","African cultures"], "answer": "Some East Asian cultures", "explanation": "'in some East Asian cultures, white is the colour of mourning'."},
            {"q": "What does red symbolise in China?", "options": ["Danger","Prohibition","Mourning","Luck and prosperity"], "answer": "Luck and prosperity", "explanation": "'red symbolises luck and prosperity in China'."},
        ]
    }
]

WRITING_TASKS = {
    "Task 1": [
        "The graph below shows the percentage of households in owned and rented accommodation in England and Wales between 1918 and 2011. Summarize the information by selecting and reporting the main features, and make comparisons where relevant. (Minimum 150 words)",
        "The diagram below shows how solar panels generate electricity for domestic use. Summarize the information by selecting and reporting the main features. (Minimum 150 words)",
        "The table below shows the number of tourists visiting three different countries over a five-year period. Summarize the information by selecting and reporting the main features, and make comparisons where relevant. (Minimum 150 words)",
    ],
    "Task 2": [
        "Some people believe that unpaid community service should be a compulsory part of high school programmes. To what extent do you agree or disagree? (Minimum 250 words)",
        "In many countries, the gap between the rich and the poor is increasing. What are the causes of this problem? What solutions can you suggest? (Minimum 250 words)",
        "Technology is increasingly being used in education. Do the advantages of this outweigh the disadvantages? (Minimum 250 words)",
        "Some people think that a sense of competition in children should be encouraged, while others believe that children who are taught to cooperate rather than compete become more useful adults. Discuss both views and give your own opinion. (Minimum 250 words)",
    ]
}

SPEAKING_TOPICS = [
    {"part": "Part 1 — Introduction", "cue": "Talk about your hometown.", "prompts": ["Where are you from?","What do you like most about your hometown?","Has your hometown changed much recently?","Would you like to live there in the future?"]},
    {"part": "Part 2 — Long Turn", "cue": "Describe a book that had a significant impact on you.", "prompts": ["What the book was about","When you read it","Why it was significant","What you learned from it"]},
    {"part": "Part 3 — Discussion", "cue": "Let's discuss reading habits in society.", "prompts": ["Do people read less now than in the past?","What are the benefits of reading for children?","How can governments encourage reading?","Will e-books replace physical books?"]},
    {"part": "Part 1 — Introduction", "cue": "Talk about your favourite type of music.", "prompts": ["What kind of music do you enjoy?","How often do you listen to music?","Do you play any instruments?","Has your taste in music changed?"]},
    {"part": "Part 2 — Long Turn", "cue": "Describe a journey or trip that was memorable.", "prompts": ["Where you went","Who you went with","What made it memorable","Whether you would go again"]},
]

LISTENING_SCRIPTS = [
    {
        "title": "City Natural History Museum — Audio Guide",
        "script": """Welcome to the City Natural History Museum. My name is Sarah, and I'll be your guide today.

The museum was founded in 1887 by Dr. James Hartley, a geologist who donated his personal collection of over 2,000 specimens to the city. Today, the museum houses more than 500,000 artifacts across four main galleries.

Our opening hours are Tuesday to Sunday, from 9 AM to 6 PM. Please note we are CLOSED on Mondays. The last entry is at 5:30 PM.

General admission is £12 for adults and £7 for children under 16. Students with a valid ID receive a 20% discount. Family tickets covering two adults and up to three children are available for £28.

Photography is permitted in all areas except the Egyptian collection on the second floor. Flash photography is not allowed anywhere in the museum.

The museum café on the ground floor serves hot and cold beverages and light meals until 5 PM. The museum shop, located near the main exit, is open during all museum hours.

For group bookings of 15 or more people, please contact our events team at least 2 weeks in advance.""",
        "questions": [
            {"q": "In what year was the museum founded?", "options": ["1877","1887","1897","1907"], "answer": "1887", "explanation": "'The museum was founded in 1887'."},
            {"q": "What is the adult admission price?", "options": ["£7","£10","£12","£15"], "answer": "£12", "explanation": "'General admission is £12 for adults'."},
            {"q": "On which day is the museum CLOSED?", "options": ["Sunday","Saturday","Tuesday","Monday"], "answer": "Monday", "explanation": "'we are CLOSED on Mondays'."},
            {"q": "Where is photography NOT permitted?", "options": ["The café","The museum shop","The Egyptian collection","The ground floor"], "answer": "The Egyptian collection", "explanation": "'except the Egyptian collection on the second floor'."},
            {"q": "How far in advance must groups of 15+ book?", "options": ["1 week","2 weeks","3 weeks","1 month"], "answer": "2 weeks", "explanation": "'please contact our events team at least 2 weeks in advance'."},
        ]
    },
    {
        "title": "University Orientation — Accommodation Office",
        "script": """Good morning, and welcome to the University Accommodation Office. I'm going to give you an overview of our on-campus housing options and the application process.

We have three types of student accommodation available. First, we have standard single rooms in shared flats. Each room has a bed, desk, wardrobe, and bookshelf. Bathrooms are shared between six students. The weekly rent is £95 per person.

Our second option is en-suite rooms. These are slightly larger, with the same furniture plus your own private bathroom. Weekly rent for en-suite rooms is £135.

Finally, we have studio apartments — fully self-contained units with a kitchenette, living area, and private bathroom. These are particularly popular with postgraduate students. Studios are priced at £195 per week.

All accommodation includes utilities — that means electricity, heating, and internet — in the price. However, please note that the university does NOT include a TV licence. You are responsible for purchasing your own if you plan to use a television or streaming services.

Applications open on the 15th of each month and close within 48 hours. We strongly recommend you apply as soon as the window opens, as demand significantly exceeds availability. Successful applicants will be notified by email within five working days.""",
        "questions": [
            {"q": "What is the weekly rent for a standard single room?", "options": ["£75","£95","£120","£135"], "answer": "£95", "explanation": "'The weekly rent is £95 per person'."},
            {"q": "Which accommodation type is most popular with postgraduate students?", "options": ["Standard single rooms","En-suite rooms","Studio apartments","Shared flats"], "answer": "Studio apartments", "explanation": "'Studios are particularly popular with postgraduate students'."},
            {"q": "What is NOT included in the accommodation price?", "options": ["Electricity","Internet","Heating","TV licence"], "answer": "TV licence", "explanation": "'the university does NOT include a TV licence'."},
            {"q": "How long does the application window remain open?", "options": ["24 hours","48 hours","72 hours","One week"], "answer": "48 hours", "explanation": "'Applications open on the 15th of each month and close within 48 hours'."},
            {"q": "Within how many working days are successful applicants notified?", "options": ["2","3","5","7"], "answer": "5", "explanation": "'notified by email within five working days'."},
        ]
    }
]

BAND_GUIDE = {
    "Band 9": ("Expert User", "Full operational command"),
    "Band 8": ("Very Good User", "Fully operational, occasional inaccuracies"),
    "Band 7": ("Good User", "Operational command with some errors"),
    "Band 6": ("Competent User", "Generally effective, some inaccuracies"),
    "Band 5": ("Modest User", "Partial command, frequent errors"),
    "Band 4": ("Limited User", "Basic competence in familiar situations"),
}

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""
    <div style='text-align:center;padding:1.25rem .75rem 1rem;'>
        <div style='font-size:2.5rem;margin-bottom:.3rem;'>✈️</div>
        <div style='font-family:Playfair Display,serif;font-size:1.3rem;font-weight:900;color:{TEXT1};'>IELTS Campus</div>
        <div style='font-size:.72rem;color:{TEXT3};margin-top:.2rem;'>Travel Through Language</div>
        <div style='height:2px;width:48px;background:{BTN_BG};border-radius:2px;margin:.8rem auto 0;'></div>
    </div>
    """, unsafe_allow_html=True)

    admin_html = "<span class='admin-badge'>👑 Admin</span>" if is_admin else ""
    st.markdown(f"""
    <div style='background:{CARD_BG};border:1px solid {BORDER};border-radius:12px;padding:.9rem 1rem;margin-bottom:.75rem;'>
        <div style='font-size:.62rem;color:{TEXT3};text-transform:uppercase;letter-spacing:.8px;font-weight:700;margin-bottom:.3rem;'>Logged In</div>
        <div style='font-size:.9rem;font-weight:800;color:{TEXT1};'>{uname}</div>
        <div style='font-size:.72rem;color:{TEXT3};margin-top:.1rem;'>{uemail}</div>
        {admin_html}
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"<p style='color:{TEXT3};font-size:.62rem;text-transform:uppercase;letter-spacing:1px;margin:.5rem 0 .4rem;padding:0 .25rem;font-weight:700;'>Navigate</p>", unsafe_allow_html=True)

    _pages = ["Home","Reading","Listening","Writing","Speaking"]
    _icons  = {"Home":"🏠","Reading":"📖","Listening":"🎧","Writing":"✍️","Speaking":"🗣️"}
    _page_map = {k.lower(): k for k in _pages}
    _url_page = st.query_params.get("page","home")
    _current  = _page_map.get(_url_page,"Home")
    section   = f"{_icons[_current]} {_current}"

    for pg in _pages:
        active  = (_current == pg)
        prefix  = "▶" if active else "  "
        if st.button(f"{prefix} {_icons[pg]}  {pg}", key=f"nav_{pg}", use_container_width=True):
            st.query_params["page"] = pg.lower()
            st.rerun()

    st.markdown("---")
    st.markdown(f"""
    <div style='background:{CARD_BG};border:1px solid {BORDER};border-radius:10px;padding:.9rem 1rem;margin-bottom:.75rem;'>
        <p style='font-size:.62rem;color:{TEXT3};margin:0 0 .6rem;text-transform:uppercase;letter-spacing:1px;font-weight:700;'>🌐 Full Website</p>
        <a href='https://ieltscampus.com' target='_blank'
           style='display:block;background:{BTN_BG};color:white !important;text-align:center;padding:.6rem;
                  border-radius:7px;text-decoration:none !important;font-weight:700;font-size:.85rem;'>
            IELTS Campus →
        </a>
    </div>
    """, unsafe_allow_html=True)

    if st.session_state.scores:
        st.markdown(f"<p style='color:{TEXT3};font-size:.62rem;text-transform:uppercase;letter-spacing:1px;margin:.5rem 0 .4rem;font-weight:700;'>📊 My Scores</p>", unsafe_allow_html=True)
        for k, v in st.session_state.scores.items():
            st.markdown(f"""
            <div style='background:{CARD_BG};border-left:3px solid {ACCENT};border:1px solid {BORDER};
                        border-left:3px solid {ACCENT};border-radius:7px;padding:.5rem .75rem;
                        margin-bottom:.4rem;font-size:.82rem;'>
                <span style='color:{TEXT3};'>{k}</span><br>
                <span style='color:{ACCENT};font-weight:800;'>{v}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("---")

    if is_admin:
        is_open = st.session_state.get("show_admin", False)
        if st.button(f"{'&#10005; Close' if is_open else '🔐 Open'} Admin Panel", key="toggle_admin", use_container_width=True):
            st.session_state["show_admin"] = not is_open; st.rerun()
        st.markdown("---")

    if st.button("🚪 Sign Out", key="sb_logout", use_container_width=True):
        tok = st.session_state.get("login_token")
        if tok: delete_token(tok)
        try: st.query_params.clear()
        except: pass
        st.session_state.authenticated = False
        st.session_state.current_user  = None
        st.session_state.login_token   = None
        st.rerun()

# ─── TOP HEADER BAR ───────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="slide-up" style="display:flex;align-items:center;justify-content:space-between;
            background:{CARD_BG};border:1px solid {BORDER};border-radius:14px;
            padding:.75rem 1.25rem;margin-bottom:1rem;gap:.75rem;">
  <div style="display:flex;align-items:center;gap:.75rem;flex:1;min-width:0;">
    <span style="font-size:1.5rem;">✈️</span>
    <span style="color:{TEXT3};font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
        IELTS Campus — Free Practice Tests &nbsp;·&nbsp; Reading · Listening · Writing · Speaking
    </span>
  </div>
  <div style="display:flex;align-items:center;gap:.6rem;flex-shrink:0;">
    <a href="https://ieltscampus.com" target="_blank"
       style="background:{BTN_BG};color:white !important;text-decoration:none !important;
              padding:.35rem .9rem;border-radius:20px;font-size:.75rem;font-weight:700;white-space:nowrap;">
        Visit Website →
    </a>
  </div>
</div>
""", unsafe_allow_html=True)

_h1, _h2, _h3, _h4 = st.columns([7, 1.1, 1.1, 1.1])
with _h2:
    sb_icon = "✕ Menu" if st.session_state.get("sidebar_open", True) else "☰ Menu"
    if st.button(sb_icon, key="sidebar_toggle_btn", use_container_width=True):
        st.session_state.sidebar_open = not st.session_state.get("sidebar_open", True)
        st.rerun()
with _h3:
    theme_lbl = "🌙 Dark" if T == "light" else "☀️ Light"
    if st.button(theme_lbl, key="theme_toggle", use_container_width=True):
        st.session_state.theme = "dark" if T == "light" else "light"; st.rerun()
with _h4:
    if st.button("🚪 Out", key="hdr_logout", use_container_width=True):
        tok = st.session_state.get("login_token")
        if tok: delete_token(tok)
        try: st.query_params.clear()
        except: pass
        st.session_state.authenticated = False
        st.session_state.current_user  = None
        st.session_state.login_token   = None
        st.rerun()

# ─── ADMIN PANEL ─────────────────────────────────────────────────────────────────
if is_admin and st.session_state.get("show_admin", False):
    all_users   = _load(USERS_FILE)
    all_activity= _load(ACTIVITY_FILE)
    today_str   = datetime.now().strftime("%Y-%m-%d")

    total_u    = len(all_users)
    banned_u   = sum(1 for u in all_users.values() if u.get("banned"))
    susp_u     = sum(1 for u in all_users.values() if u.get("suspended"))
    frozen_u   = sum(1 for u in all_users.values() if u.get("frozen"))
    verified_u = sum(1 for u in all_users.values() if u.get("verified"))
    new_today  = sum(1 for u in all_users.values() if u.get("signup_date","")[:10] == today_str)
    online_u   = sum(1 for u in all_users.values() if get_online_status(u)["online"])

    st.markdown(f"""
    <div style='background:linear-gradient(135deg,{ACCENT_DIM},{INFO_BG});
                border:2px solid {ACCENT_BORDER};border-radius:18px;
                padding:1.25rem 1.75rem;margin-bottom:1.25rem;
                display:flex;align-items:center;gap:1rem;'>
        <span style='font-size:2.2rem;'>🔐</span>
        <div>
            <div style='font-size:1.1rem;font-weight:900;color:{ACCENT};font-family:Playfair Display,serif;'>Admin Control Panel</div>
            <div style='font-size:.75rem;color:{TEXT3};'>{uname} ({uemail})</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    sc = st.columns(8)
    for col, lbl, val, clr in [
        (sc[0],"👥 Total",   total_u,   TEXT1),
        (sc[1],"🟢 Online",  online_u,  "#4ade80"),
        (sc[2],"🆕 Today",   new_today, "#fbbf24"),
        (sc[3],"🚫 Banned",  banned_u,  "#f87171"),
        (sc[4],"⏸️ Susp.",   susp_u,    "#fbbf24"),
        (sc[5],"🧊 Frozen",  frozen_u,  "#60a5fa"),
        (sc[6],"✔️ Verified",verified_u,"#4ade80"),
        (sc[7],"🛡️ Mods",    sum(1 for u in all_users.values() if u.get("role")=="moderator"),"#c084fc"),
    ]:
        with col:
            st.markdown(f"""<div style='background:{CARD_BG};border:1px solid {BORDER};border-left:3px solid {clr};
                border-radius:10px;padding:.7rem .9rem;text-align:center;'>
                <div style='font-size:.58rem;color:{TEXT3};text-transform:uppercase;font-weight:800;'>{lbl}</div>
                <div style='font-size:1.6rem;font-weight:900;color:{clr};font-family:Playfair Display,serif;'>{val}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)

    sf1, sf2, sf3 = st.columns([3,1,1])
    with sf1: adm_search = st.text_input("🔍 Search name / email", key="adm_search", placeholder="Search...")
    with sf2: adm_filter = st.selectbox("Status", ["All","Active","Banned","Suspended","Frozen","Verified","Online"], key="adm_filter")
    with sf3: adm_role_f = st.selectbox("Role", ["All","Admin","Moderator","User"], key="adm_role_f")

    adm_tab1, adm_tab2 = st.tabs([f"👥 All Users ({total_u})", "📊 Activity Monitor"])

    with adm_tab1:
        for em, ud in sorted(all_users.items(), key=lambda x: x[1].get("signup_date",""), reverse=True):
            if adm_search and adm_search.lower() not in em.lower() and adm_search.lower() not in ud.get("name","").lower():
                continue
            is_b  = ud.get("banned",False); is_s = ud.get("suspended",False)
            is_fr = ud.get("frozen",False); is_ro = ud.get("readonly",False)
            is_ve = ud.get("verified",False); u_role = ud.get("role","user")
            online_st = get_online_status(ud)

            if adm_filter=="Banned"    and not is_b:               continue
            if adm_filter=="Suspended" and not is_s:               continue
            if adm_filter=="Active"    and (is_b or is_s or is_fr): continue
            if adm_filter=="Frozen"    and not is_fr:               continue
            if adm_filter=="Verified"  and not is_ve:               continue
            if adm_filter=="Online"    and not online_st["online"]: continue
            if adm_role_f=="Admin"     and em not in ADMIN_EMAILS:  continue
            if adm_role_f=="Moderator" and u_role!="moderator":    continue
            if adm_role_f=="User"      and (em in ADMIN_EMAILS or u_role=="moderator"): continue

            badges = ""
            if is_b:  badges += f"<span style='background:rgba(248,113,113,0.15);color:#f87171;border:1px solid rgba(248,113,113,0.4);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>🚫 BANNED</span>"
            if is_s:  badges += f"<span style='background:rgba(251,191,36,0.15);color:#fbbf24;border:1px solid rgba(251,191,36,0.4);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>⏸️ SUSP</span>"
            if is_fr: badges += f"<span style='background:rgba(96,165,250,0.15);color:#60a5fa;border:1px solid rgba(96,165,250,0.4);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>🧊 FROZEN</span>"
            if is_ro: badges += f"<span style='background:rgba(251,191,36,0.15);color:#fbbf24;border:1px solid rgba(251,191,36,0.4);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>👁️ RO</span>"
            if is_ve: badges += f"<span style='background:rgba(74,222,128,0.12);color:#4ade80;border:1px solid rgba(74,222,128,0.35);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>✔️ VER</span>"
            if em in ADMIN_EMAILS: badges += f"<span style='background:{ACCENT_DIM};color:{ACCENT};border:1px solid {ACCENT_BORDER};border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>👑 ADMIN</span>"
            if u_role=="moderator": badges += f"<span style='background:rgba(192,132,252,0.15);color:#c084fc;border:1px solid rgba(192,132,252,0.4);border-radius:5px;padding:.1rem .45rem;font-size:.62rem;font-weight:800;margin-left:.3rem'>🛡️ MOD</span>"

            row_bdr = "#f87171" if is_b else "#60a5fa" if is_fr else "#fbbf24" if is_s else BORDER
            st.markdown(f"""
            <div style='background:{CARD_BG};border:1px solid {row_bdr};border-left:4px solid {row_bdr};
                        border-radius:10px;padding:.75rem 1rem;margin-bottom:.4rem;
                        display:flex;align-items:center;gap:.9rem;flex-wrap:wrap;'>
                <div style='flex:1;min-width:160px;'>
                    <div style='font-weight:800;color:{TEXT1};font-size:.86rem;'>{ud.get("name","?")} {badges}</div>
                    <div style='font-size:.7rem;color:{TEXT3};margin-top:.1rem;'>{em}</div>
                </div>
                <div style='font-size:.7rem;color:{online_st["color"]};font-weight:700;'>{online_st["dot"]} {online_st["label"]}</div>
                <div style='font-size:.68rem;color:{TEXT3};text-align:right;'>
                    <div>🔢 {ud.get("login_count",0)} logins</div>
                    <div>📅 {ud.get("signup_date","—")[:10]}</div>
                </div>
            </div>""", unsafe_allow_html=True)

            with st.expander(f"⚙️ Manage — {ud.get('name','?')} ({em})"):
                mt1, mt2, mt3, mt4 = st.tabs(["📋 Profile & Controls", "🧊 Freeze/Read-Only", "🗑️ Delete", "📜 Activity"])

                with mt1:
                    p1, p2, p3 = st.columns(3)
                    with p1:
                        for lbl2, val2 in [("Email",em),("Joined",ud.get("signup_date","—")[:10]),
                                          ("Last Login",ud.get("last_login","—")[:16]),
                                          ("Logins",ud.get("login_count",0))]:
                            st.markdown(f'<div style="font-size:.76rem;color:{TEXT2};padding:.2rem 0;border-bottom:1px solid {BORDER};">📌 <b>{lbl2}:</b> {val2}</div>', unsafe_allow_html=True)
                        new_note = st.text_area("📝 Admin Note", value=ud.get("admin_note",""), key=f"anote_{em}", height=65)
                        if st.button("💾 Save Note", key=f"save_anote_{em}"):
                            udb2 = _load(USERS_FILE); udb2[em]["admin_note"] = new_note; _save(USERS_FILE, udb2)
                            st.success("Saved!"); st.rerun()

                    with p2:
                        # Ban
                        if is_b:
                            st.error(f"🚫 Banned: {ud.get('ban_reason','—')}")
                            if st.button("✅ Unban", key=f"unban_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["banned"]=False; udb2[em]["ban_reason"]=""
                                _save(USERS_FILE, udb2)
                                _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                                st.success("Unbanned!"); st.rerun()
                        else:
                            ban_r = st.text_input("Ban reason", key=f"banr_{em}", placeholder="e.g. Spam")
                            if st.button("🚫 Ban", key=f"ban_{em}"):
                                udb2=_load(USERS_FILE); udb2[em].update({"banned":True,"ban_reason":ban_r,"banned_at":now_str()})
                                _save(USERS_FILE, udb2)
                                _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                                log_activity(em,"banned",f"Banned: {ban_r}"); st.error("🚫 Banned!"); st.rerun()
                        st.markdown("---")
                        # Suspend
                        if is_s:
                            st.warning(f"⏸️ Until: {ud.get('suspended_until','—')}")
                            if st.button("▶️ Lift Suspension", key=f"unsus_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["suspended"]=False; udb2[em]["suspended_until"]=""
                                _save(USERS_FILE, udb2); st.success("Lifted!"); st.rerun()
                        else:
                            sus_d = st.number_input("Days",1,365,7,key=f"susd_{em}")
                            if st.button("⏸️ Suspend", key=f"sus_{em}"):
                                until = (datetime.now()+timedelta(days=int(sus_d))).strftime("%Y-%m-%d")
                                udb2=_load(USERS_FILE); udb2[em].update({"suspended":True,"suspended_until":until})
                                _save(USERS_FILE, udb2)
                                _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                                st.warning(f"Suspended {sus_d}d!"); st.rerun()

                    with p3:
                        # Verify
                        if is_ve:
                            if st.button("❌ Remove Verification", key=f"unverify_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["verified"]=False; _save(USERS_FILE, udb2)
                                st.warning("Removed!"); st.rerun()
                        else:
                            if st.button("✔️ Verify", key=f"verify_{em}"):
                                udb2=_load(USERS_FILE); udb2[em].update({"verified":True,"verified_at":now_str()})
                                _save(USERS_FILE, udb2); st.success("✔️ Verified!"); st.rerun()
                        st.markdown("---")
                        # Moderator
                        if u_role=="moderator":
                            if st.button("👤 Remove Moderator", key=f"unmod_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["role"]="user"; _save(USERS_FILE, udb2)
                                st.success("Removed!"); st.rerun()
                        else:
                            if st.button("🛡️ Make Moderator", key=f"makemod_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["role"]="moderator"; _save(USERS_FILE, udb2)
                                st.success("🛡️ Moderator!"); st.rerun()
                        st.markdown("---")
                        # Password reset
                        new_pw = st.text_input("New Password", type="password", key=f"rpw_{em}")
                        if st.button("🔑 Reset Password", key=f"do_rpw_{em}"):
                            if new_pw and len(new_pw)>=6:
                                udb2=_load(USERS_FILE); udb2[em]["password_hash"]=hash_pw(new_pw)
                                _save(USERS_FILE, udb2)
                                _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                                st.success("✅ Reset!"); st.rerun()
                            else: st.error("Min 6 chars.")

                with mt2:
                    fr1, fr2 = st.columns(2)
                    with fr1:
                        st.markdown(f'<div style="font-size:.72rem;font-weight:800;text-transform:uppercase;color:#60a5fa;margin-bottom:.4rem;">🧊 Freeze</div>', unsafe_allow_html=True)
                        if is_fr:
                            st.info(f"Frozen: {ud.get('frozen_reason','—')}")
                            if st.button("🔓 Unfreeze", key=f"unfreeze_{em}"):
                                udb2=_load(USERS_FILE); udb2[em].update({"frozen":False,"frozen_at":"","frozen_reason":""})
                                _save(USERS_FILE, udb2); st.success("✅ Unfrozen!"); st.rerun()
                        else:
                            fr_r = st.text_input("Freeze reason", key=f"freezer_{em}")
                            if st.button("🧊 Freeze", key=f"freeze_{em}", type="primary"):
                                udb2=_load(USERS_FILE); udb2[em].update({"frozen":True,"frozen_at":now_str(),"frozen_reason":fr_r})
                                _save(USERS_FILE, udb2)
                                _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                                st.info("🧊 Frozen!"); st.rerun()
                    with fr2:
                        st.markdown(f'<div style="font-size:.72rem;font-weight:800;text-transform:uppercase;color:#fbbf24;margin-bottom:.4rem;">👁️ Read-Only</div>', unsafe_allow_html=True)
                        if is_ro:
                            st.warning("👁️ Currently Read-Only")
                            if st.button("✅ Restore Full Access", key=f"unro_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["readonly"]=False; _save(USERS_FILE, udb2)
                                st.success("✅ Restored!"); st.rerun()
                        else:
                            if st.button("👁️ Set Read-Only", key=f"setro_{em}"):
                                udb2=_load(USERS_FILE); udb2[em]["readonly"]=True; _save(USERS_FILE, udb2)
                                st.warning("👁️ Read-only set!"); st.rerun()

                with mt3:
                    st.warning("⚠️ Permanent — cannot be undone!")
                    confirm_del = st.text_input("Type email to confirm", key=f"delconf_{em}", placeholder=em)
                    if st.button("🗑️ DELETE PERMANENTLY", key=f"del_{em}"):
                        if confirm_del.strip()==em:
                            udb2=_load(USERS_FILE); udb2.pop(em,None); _save(USERS_FILE, udb2)
                            _save(TOKENS_FILE, {t:v for t,v in _load(TOKENS_FILE).items() if v.get("email")!=em})
                            adb2=_load(ACTIVITY_FILE); adb2.pop(em,None); _save(ACTIVITY_FILE, adb2)
                            st.error(f"🗑️ {em} deleted!"); st.rerun()
                        else: st.error("❌ Email doesn't match.")

                with mt4:
                    user_acts = all_activity.get(em, [])
                    if not user_acts:
                        st.info("No activity logged.")
                    else:
                        for ev in reversed(user_acts[-30:]):
                            a_c = "#4ade80" if "signup" in ev.get("action","") else "#f87171" if "ban" in ev.get("action","") else "#60a5fa" if "signin" in ev.get("action","") else TEXT2
                            st.markdown(f'<div style="font-size:.7rem;color:{a_c};padding:.2rem 0;border-bottom:1px solid {BORDER};">{ev.get("time","")[:16]} · <b>{ev.get("action","")}</b> — {ev.get("detail","")}</div>', unsafe_allow_html=True)
                    if st.button("🗑️ Clear Activity", key=f"clract_{em}"):
                        adb2=_load(ACTIVITY_FILE); adb2.pop(em,None); _save(ACTIVITY_FILE, adb2)
                        st.success("Cleared!"); st.rerun()

    with adm_tab2:
        all_acts_flat = []
        for em_a, acts in all_activity.items():
            for ev in acts:
                all_acts_flat.append({**ev, "email": em_a, "name": all_users.get(em_a,{}).get("name","?")})
        all_acts_flat = sorted(all_acts_flat, key=lambda x: x.get("time",""), reverse=True)

        _today_acts = [a for a in all_acts_flat if a.get("time","")[:10]==today_str]
        am_cols = st.columns(6)
        for col, lbl, val, clr in [
            (am_cols[0],"🟢 Online",   online_u, "#4ade80"),
            (am_cols[1],"🔑 Logins",   sum(1 for a in _today_acts if a.get("action")=="signin"), "#60a5fa"),
            (am_cols[2],"✨ Signups",   sum(1 for a in _today_acts if a.get("action")=="signup"), "#4ade80"),
            (am_cols[3],"🧊 Frozen",    frozen_u, "#60a5fa"),
            (am_cols[4],"👁️ Read-Only", sum(1 for u in all_users.values() if u.get("readonly")), "#fbbf24"),
            (am_cols[5],"📋 Total Evts",len(all_acts_flat), "#9ca3af"),
        ]:
            with col:
                st.markdown(f'<div style="background:{CARD_BG};border:1px solid {BORDER};border-radius:10px;padding:.6rem .9rem;text-align:center;"><div style="font-size:.6rem;color:{TEXT3};text-transform:uppercase;font-weight:700;">{lbl}</div><div style="font-size:1.5rem;font-weight:900;color:{clr};font-family:Playfair Display,serif;">{val}</div></div>', unsafe_allow_html=True)

        am_srch = st.text_input("🔍 Filter activity", key="am_srch", placeholder="e.g. signin, banned...")
        shown2  = 0
        for ev in all_acts_flat[:300]:
            if am_srch and am_srch.lower() not in ev.get("email","").lower() and am_srch.lower() not in ev.get("action","").lower(): continue
            ac  = ev.get("action","")
            a_c = "#4ade80" if ac in ["signup","verified"] else "#f87171" if ac in ["banned","frozen"] else "#fbbf24" if ac in ["suspended","readonly_set"] else "#60a5fa" if ac=="signin" else TEXT3
            st.markdown(f'<div style="display:flex;gap:.6rem;align-items:center;padding:.22rem 0;border-bottom:1px solid {BORDER};"><span style="font-size:.64rem;color:{TEXT3};min-width:105px;">{ev.get("time","")[:16]}</span><span style="font-size:.7rem;font-weight:700;color:{a_c};min-width:120px;">{ac}</span><span style="font-size:.7rem;color:{TEXT2};min-width:150px;">{ev.get("name","?")} ({ev.get("email","")[:18]})</span><span style="font-size:.66rem;color:{TEXT3};">{ev.get("detail","")[:50]}</span></div>', unsafe_allow_html=True)
            shown2 += 1
            if shown2 >= 100: break

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
    st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# HOME
# ═══════════════════════════════════════════════════════════════════════════════
if section == "🏠 Home":
    st.markdown(f"""
    <div class="vibe-header slide-up">
      <h1>IELTS Campus</h1>
      <p class="tagline">Free exam-style practice across all four modules — Reading, Listening, Writing & Speaking</p>
      <div class="pill-row">
        <span class="pill pill-a">📖 Reading</span>
        <span class="pill pill-b">🎧 Listening</span>
        <span class="pill pill-c">✍️ Writing</span>
        <span class="pill pill-a">🗣️ Speaking</span>
        <span class="pill pill-b">✈️ Travel Through Language</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    for col, icon, title, desc, page in [
        (col1,"📖","Reading","3 passages · 12 questions · IELTS-style MCQs","reading"),
        (col2,"🎧","Listening","2 recordings · 10 questions · Audio simulation","listening"),
        (col3,"✍️","Writing","Task 1 + Task 2 · AI feedback on structure & vocab","writing"),
        (col4,"🗣️","Speaking","3 parts · Record your response · Instant analysis","speaking"),
    ]:
        with col:
            st.markdown(f"""
            <div class="module-card">
                <div style='font-size:2.4rem;margin-bottom:.6rem;'>{icon}</div>
                <div style='font-size:1.05rem;font-weight:800;margin:.4rem 0 .3rem;color:white;font-family:Playfair Display,serif;'>{title}</div>
                <div style='font-size:.78rem;opacity:.7;color:#d0c8c0;'>{desc}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
    st.markdown(f"""<div class="section-head"><div class="icon-wrap">📊</div><h3>IELTS Band Score Guide</h3></div>""", unsafe_allow_html=True)

    cols = st.columns(3)
    for i, (band, (level, desc)) in enumerate(BAND_GUIDE.items()):
        with cols[i % 3]:
            st.markdown(f"""
            <div style='background:{CARD_BG};border-left:4px solid {BAND_COLORS[i]};border:1px solid {BORDER};
                        border-left:4px solid {BAND_COLORS[i]};border-radius:10px;padding:.9rem 1rem;margin-bottom:.75rem;'>
                <strong style='color:{BAND_COLORS[i]};font-family:Playfair Display,serif;font-size:1.05rem;'>{band}</strong><br>
                <span style='font-size:.82rem;font-weight:700;color:{TEXT1};'>{level}</span><br>
                <span style='font-size:.78rem;color:{TEXT3};'>{desc}</span>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns(4)
    for col, icon, title, desc in [
        (f1,"🎯","Smart Scoring","Automatic scoring with band estimation and per-question feedback"),
        (f2,"💡","Exam Tips","Each module includes IELTS-specific strategy tips"),
        (f3,"📈","Track Progress","Your scores are saved in the sidebar across modules"),
        (f4,"🏫","Expert Coaching","Join live classes with qualified IELTS instructors"),
    ]:
        with col:
            st.markdown(f"""<div class="feature-card">
                <div class="fc-icon">{icon}</div>
                <h3>{title}</h3><p>{desc}</p>
            </div>""", unsafe_allow_html=True)

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style='background:{MOD_BG};border-radius:16px;padding:1.75rem 2rem;text-align:center;
                border-left:5px solid {ACCENT};border:1px solid {"#1c1c1c" if T=="dark" else "#2d1200"};
                border-left:5px solid {ACCENT};'>
        <h4 style='color:{ACCENT};margin:0 0 .5rem;font-family:Playfair Display,serif;font-size:1.3rem;'>🏫 Want Expert Coaching?</h4>
        <p style='color:#9ca3af;margin:0 0 1rem;font-size:.9rem;'>Join IELTS Campus for live classes, mock tests, and personalised feedback from expert instructors.</p>
        <a href='https://ieltscampus.com/courses/' target='_blank'
           style='background:{BTN_BG};color:white !important;padding:.65rem 1.5rem;border-radius:10px;
                  text-decoration:none !important;font-weight:700;font-size:.9rem;'>
            Explore Courses →
        </a>
    </div>""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# READING
# ═══════════════════════════════════════════════════════════════════════════════
elif section == "📖 Reading":
    st.markdown(f"""
    <div class="vibe-header slide-up">
      <h1>📖 Reading</h1>
      <p class="tagline">Read the passage carefully and answer all questions. Tests identification of main ideas, details, opinions, and implied meanings.</p>
      <div class="pill-row">
        <span class="pill pill-a">IELTS Academic</span>
        <span class="pill pill-b">Multiple Choice</span>
        <span class="pill pill-c">Instant Scoring</span>
      </div>
    </div>""", unsafe_allow_html=True)

    pid = st.selectbox("Choose Passage:", range(len(READING_PASSAGES)),
                       format_func=lambda i: f"Passage {i+1}: {READING_PASSAGES[i]['title']}")
    passage = READING_PASSAGES[pid]

    with st.expander("📄 Read Passage", expanded=True):
        st.markdown(f"#### {passage['title']}")
        for para in passage["passage"].strip().split("\n\n"):
            st.markdown(f'<p style="color:{TEXT2};line-height:1.85;font-size:.95rem;margin-bottom:1rem;">{para.strip()}</p>', unsafe_allow_html=True)

    st.markdown(f"""<div class="section-head"><div class="icon-wrap">📝</div><h3>Answer the Questions</h3></div>""", unsafe_allow_html=True)
    user_answers = {}
    for i, q in enumerate(passage["questions"]):
        st.markdown(f"**Q{i+1}. {q['q']}**")
        user_answers[i] = st.radio("", q["options"], key=f"r_{pid}_{i}", index=None, label_visibility="collapsed")
        st.markdown("")

    if st.button("✅ Submit Answers", key="reading_submit", disabled=is_readonly):
        correct = sum(1 for i, q in enumerate(passage["questions"]) if user_answers.get(i) == q["answer"])
        total   = len(passage["questions"])
        band    = round(4.0 + (correct/total)*5.0, 1)
        st.session_state.scores["📖 Reading"] = f"{correct}/{total} (Band ~{band})"

        st.markdown(f"""<div class="trophy-banner slide-up">
            <div class="trophy-icon">{"🏆" if correct==total else "📊"}</div>
            <div class="trophy-text">
                <h2>{'Perfect Score!' if correct==total else 'Results'}</h2>
                <p>Passage: {passage['title']}</p>
            </div>
            <div class="trophy-score">
                <div class="ts-label">Score</div>
                <div class="ts-value">{correct}/{total}</div>
                <div style="font-size:.72rem;color:{TEXT3};margin-top:.2rem;">Band ~{band}</div>
            </div>
        </div>""", unsafe_allow_html=True)

        for i, q in enumerate(passage["questions"]):
            ua = user_answers.get(i)
            if ua == q["answer"]:
                st.success(f"✅ **Q{i+1}:** Correct! — {q['explanation']}")
            else:
                st.error(f"❌ **Q{i+1}:** Your answer: *{ua}* | Correct: **{q['answer']}** — {q['explanation']}")

        st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
        tips = [
            "💡 **Skim first, then scan** — Read the questions before the passage on the real test.",
            "💡 **Keywords** — Underline key terms in questions and locate them in the passage.",
            "💡 **Don't overthink** — The answer is always in the text, not from general knowledge.",
        ]
        for tip in tips:
            st.info(tip)


# ═══════════════════════════════════════════════════════════════════════════════
# LISTENING
# ═══════════════════════════════════════════════════════════════════════════════
elif section == "🎧 Listening":
    st.markdown(f"""
    <div class="vibe-header slide-up">
      <h1>🎧 Listening</h1>
      <p class="tagline">In a real IELTS test you hear an audio recording. Here, read the transcript carefully (simulating listening) then answer the questions.</p>
      <div class="pill-row">
        <span class="pill pill-a">Audio Simulation</span>
        <span class="pill pill-b">Form Completion</span>
        <span class="pill pill-c">Multiple Choice</span>
      </div>
    </div>""", unsafe_allow_html=True)

    script_idx = st.selectbox("Choose Recording:", range(len(LISTENING_SCRIPTS)),
                              format_func=lambda i: f"Recording {i+1}: {LISTENING_SCRIPTS[i]['title']}")
    script = LISTENING_SCRIPTS[script_idx]

    st.markdown(f"### 📻 {script['title']}")
    with st.expander("🎙️ View Audio Transcript — Read carefully as you would listen", expanded=True):
        for line in script["script"].strip().split("\n\n"):
            st.markdown(f'<p style="color:{TEXT2};line-height:1.85;font-size:.93rem;margin-bottom:.75rem;">{line.strip()}</p>', unsafe_allow_html=True)

    st.markdown(f"""<div class="section-head"><div class="icon-wrap">📝</div><h3>Answer the Questions</h3></div>""", unsafe_allow_html=True)
    user_answers = {}
    for i, q in enumerate(script["questions"]):
        st.markdown(f"**Q{i+1}. {q['q']}**")
        user_answers[i] = st.radio("", q["options"], key=f"l_{script_idx}_{i}", index=None, label_visibility="collapsed")
        st.markdown("")

    if st.button("✅ Submit Answers", key="listening_submit", disabled=is_readonly):
        correct = sum(1 for i, q in enumerate(script["questions"]) if user_answers.get(i) == q["answer"])
        total   = len(script["questions"])
        band    = round(4.0 + (correct/total)*5.0, 1)
        st.session_state.scores["🎧 Listening"] = f"{correct}/{total} (Band ~{band})"

        st.markdown(f"""<div class="trophy-banner slide-up">
            <div class="trophy-icon">{"🏆" if correct==total else "📊"}</div>
            <div class="trophy-text">
                <h2>{'Excellent!' if correct==total else 'Results'}</h2>
                <p>{script['title']}</p>
            </div>
            <div class="trophy-score">
                <div class="ts-label">Score</div>
                <div class="ts-value">{correct}/{total}</div>
                <div style="font-size:.72rem;color:{TEXT3};margin-top:.2rem;">Band ~{band}</div>
            </div>
        </div>""", unsafe_allow_html=True)

        for i, q in enumerate(script["questions"]):
            ua = user_answers.get(i)
            if ua == q["answer"]:
                st.success(f"✅ **Q{i+1}:** Correct! — {q['explanation']}")
            else:
                st.error(f"❌ **Q{i+1}:** Your answer: *{ua}* | Correct: **{q['answer']}** — {q['explanation']}")

        st.info("🎧 **Listening Tip:** In the real test, you hear the audio ONCE. Train yourself not to pause or replay recordings during practice.")


# ═══════════════════════════════════════════════════════════════════════════════
# WRITING
# ═══════════════════════════════════════════════════════════════════════════════
elif section == "✍️ Writing":
    st.markdown(f"""
    <div class="vibe-header slide-up">
      <h1>✍️ Writing</h1>
      <p class="tagline">Complete both Task 1 and Task 2. Receive automated feedback on Task Achievement, Coherence, Lexical Resource, and Grammar.</p>
      <div class="pill-row">
        <span class="pill pill-a">Task Achievement</span>
        <span class="pill pill-b">Coherence & Cohesion</span>
        <span class="pill pill-c">Lexical Resource</span>
        <span class="pill pill-a">Grammar</span>
      </div>
    </div>""", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["📊 Task 1 — Describe Visual Data", "📝 Task 2 — Extended Essay"])

    with tab1:
        if "t1_prompt" not in st.session_state:
            st.session_state.t1_prompt = random.choice(WRITING_TASKS["Task 1"])
        task1_prompt = st.session_state.t1_prompt

        st.markdown(f"<div class='section-card'><p class='section-title'>Task 1</p><p class='section-desc'>{task1_prompt}</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='timer-box'>⏱️ Recommended: 20 minutes</div>", unsafe_allow_html=True)

        col_new, _ = st.columns([1,5])
        with col_new:
            if st.button("🔄 New Task", key="new_t1"):
                st.session_state.t1_prompt = random.choice(WRITING_TASKS["Task 1"]); st.rerun()

        response1 = st.text_area("Your Response:", height=240,
            placeholder="Write your Task 1 response here... (minimum 150 words)", key="writing_t1")

        if st.button("📊 Analyse Task 1", key="analyze_t1", disabled=is_readonly):
            if response1.strip():
                wc = len(response1.split())
                st.markdown(f"**Word Count:** {wc} {'✅' if wc >= 150 else '⚠️ (needs 150+)'}")

                words_lower = response1.lower().split()
                vocab_set   = set(words_lower)
                lex_ratio   = len(vocab_set)/max(len(words_lower),1)

                score_ta = min(9, 4 + (1 if wc>=150 else 0)
                              + (1 if any(w in response1.lower() for w in ["shows","indicates","represents","depicts","illustrates"]) else 0)
                              + (1 if any(w in response1.lower() for w in ["overall","in summary","generally","in conclusion"]) else 0))
                score_cc = min(9, 4 + (1 if any(w in response1.lower() for w in ["however","furthermore","moreover","additionally","in contrast","on the other hand"]) else 0)
                              + (1 if any(w in response1.lower() for w in ["firstly","secondly","finally","consequently"]) else 0))
                score_lr = min(9, 4 + (1 if lex_ratio > 0.5 else 0)
                              + (1 if any(w in response1.lower() for w in ["significantly","notably","substantially","marginally","dramatically","considerably"]) else 0))
                score_gr = min(9, 5 + (1 if response1.count(",") > 3 else 0))
                overall  = round((score_ta+score_cc+score_lr+score_gr)/4, 1)
                st.session_state.scores["✍️ Writing T1"] = f"Band ~{overall}"

                st.markdown(f"""<div class="trophy-banner slide-up">
                    <div class="trophy-icon">✍️</div>
                    <div class="trophy-text"><h2>Task 1 Analysis</h2><p>{wc} words</p></div>
                    <div class="trophy-score"><div class="ts-label">Overall Band</div><div class="ts-value">{overall}</div></div>
                </div>""", unsafe_allow_html=True)

                m1,m2,m3,m4 = st.columns(4)
                for col, lbl, sc in [(m1,"Task Achievement",score_ta),(m2,"Coherence",score_cc),(m3,"Lexical Resource",score_lr),(m4,"Grammar",score_gr)]:
                    with col: st.metric(lbl, sc)

                st.markdown("---")
                if wc < 150: st.warning("📏 Below 150 words. You will be penalised in IELTS for not meeting the word count.")
                if not any(w in response1.lower() for w in ["overall","in summary","generally"]): st.info("📌 **Tip:** Always include an overview sentence summarising the main trend/pattern.")
                if not any(w in response1.lower() for w in ["however","furthermore","moreover","additionally"]): st.info("🔗 **Tip:** Use cohesive devices to link ideas (however, furthermore, in addition).")
                if score_lr < 6: st.info("📚 **Tip:** Vary your vocabulary — avoid repeating the same words. Use synonyms and paraphrases.")
                st.success("✅ Well done for completing Task 1! Consistent practice is key to Band 7+.")
            else:
                st.warning("Please write your response first.")

    with tab2:
        if "t2_prompt" not in st.session_state:
            st.session_state.t2_prompt = random.choice(WRITING_TASKS["Task 2"])
        task2_prompt = st.session_state.t2_prompt

        st.markdown(f"<div class='section-card'><p class='section-title'>Task 2</p><p class='section-desc'>{task2_prompt}</p></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='timer-box'>⏱️ Recommended: 40 minutes</div>", unsafe_allow_html=True)

        col_new2, _ = st.columns([1,5])
        with col_new2:
            if st.button("🔄 New Task", key="new_t2"):
                st.session_state.t2_prompt = random.choice(WRITING_TASKS["Task 2"]); st.rerun()

        response2 = st.text_area("Your Response:", height=340,
            placeholder="Write your Task 2 essay here... (minimum 250 words)", key="writing_t2")

        if st.button("📊 Analyse Task 2", key="analyze_t2", disabled=is_readonly):
            if response2.strip():
                wc = len(response2.split())
                paras = [p.strip() for p in response2.split('\n\n') if p.strip()]
                words_lower = response2.lower().split()
                vocab_set   = set(words_lower)
                lex_ratio   = len(vocab_set)/max(len(words_lower),1)

                score_ta = min(9, 4 + (1 if wc>=250 else 0)
                              + (1 if any(w in response2.lower() for w in ["i believe","in my opinion","i argue","i contend","i maintain"]) else 0)
                              + (1 if len(paras)>=3 else 0))
                score_cc = min(9, 4 + (len(paras)>=4)
                              + any(w in response2.lower() for w in ["however","furthermore","moreover","in addition","consequently"])
                              + any(w in response2.lower() for w in ["in conclusion","to conclude","to sum up","in summary"]))
                score_lr = min(9, 4 + (1 if lex_ratio>0.55 else 0)
                              + (1 if any(w in response2.lower() for w in ["significant","crucial","inevitable","beneficial","detrimental","paramount","fundamental"]) else 0))
                score_gr = min(9, 5 + (1 if response2.count(",")>4 else 0))
                overall  = round((score_ta+score_cc+score_lr+score_gr)/4, 1)
                st.session_state.scores["✍️ Writing T2"] = f"Band ~{overall}"

                st.markdown(f"""<div class="trophy-banner slide-up">
                    <div class="trophy-icon">📝</div>
                    <div class="trophy-text"><h2>Task 2 Analysis</h2><p>{wc} words · {len(paras)} paragraphs</p></div>
                    <div class="trophy-score"><div class="ts-label">Overall Band</div><div class="ts-value">{overall}</div></div>
                </div>""", unsafe_allow_html=True)

                m1,m2,m3,m4 = st.columns(4)
                for col, lbl, sc in [(m1,"Task Achievement",score_ta),(m2,"Coherence",score_cc),(m3,"Lexical Resource",score_lr),(m4,"Grammar",score_gr)]:
                    with col: st.metric(lbl, sc)

                st.markdown("---")
                if wc < 250: st.warning("📏 Below 250 words. IELTS penalises short responses heavily.")
                if len(paras) < 4: st.info("📋 **Structure:** Aim for 4–5 paragraphs: Introduction → 2–3 Body Paragraphs → Conclusion.")
                if not any(w in response2.lower() for w in ["in conclusion","to conclude","to sum up"]): st.info("🔚 **Tip:** Always end with a clear conclusion paragraph.")
                if lex_ratio < 0.55: st.info("📚 **Tip:** Expand your vocabulary. Avoid repeating words — use synonyms and collocations.")
                st.success("✅ Great effort! Regular writing practice is the key to Band 7+. 🏆")
            else:
                st.warning("Please write your essay first.")


# ═══════════════════════════════════════════════════════════════════════════════
# SPEAKING
# ═══════════════════════════════════════════════════════════════════════════════
elif section == "🗣️ Speaking":
    st.markdown(f"""
    <div class="vibe-header slide-up">
      <h1>🗣️ Speaking</h1>
      <p class="tagline">The IELTS Speaking test has 3 parts. Press the microphone, speak your answer aloud, then receive instant feedback on fluency and duration.</p>
      <div class="pill-row">
        <span class="pill pill-a">Part 1: Introduction</span>
        <span class="pill pill-b">Part 2: Long Turn</span>
        <span class="pill pill-c">Part 3: Discussion</span>
      </div>
    </div>""", unsafe_allow_html=True)

    topic_idx = st.selectbox("Choose Speaking Topic:", range(len(SPEAKING_TOPICS)),
                             format_func=lambda i: f"{SPEAKING_TOPICS[i]['part']}: {SPEAKING_TOPICS[i]['cue'][:45]}...")
    topic = SPEAKING_TOPICS[topic_idx]

    st.markdown(f"""
    <div class="topic-card slide-up">
        <h4>🎯 {topic['part']}</h4>
        <p class="cue">"{topic['cue']}"</p>
        <p style='color:{TEXT3};font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin:.5rem 0 .25rem;'>Talk about:</p>
        <ul>
            {''.join(f"<li>{p}</li>" for p in topic['prompts'])}
        </ul>
    </div>""", unsafe_allow_html=True)

    part = topic["part"]
    if "Part 1" in part:   min_good, ideal, t_label = 20, 45, "45 seconds"
    elif "Part 2" in part: min_good, ideal, t_label = 60, 120, "1–2 minutes"
    else:                  min_good, ideal, t_label = 30, 75, "75 seconds"

    st.markdown(f"<div class='timer-box'>⏱️ Target: {t_label} &nbsp;·&nbsp; Preparation: 1 minute</div>", unsafe_allow_html=True)

    st.markdown(f"""<div class="section-head"><div class="icon-wrap">🎙️</div><h3>Record Your Response</h3></div>""", unsafe_allow_html=True)

    if "speaking_deleted" not in st.session_state:
        st.session_state.speaking_deleted = False
    if st.session_state.speaking_deleted:
        st.session_state.speaking_deleted = False

    audio_data = st.audio_input("🎤 Press to record:", key=f"mic_{topic_idx}")

    analyze = False
    duration_seconds = 0

    if audio_data is not None:
        st.audio(audio_data, format="audio/wav")
        ca, cd, _ = st.columns([1,1,4])
        with ca:
            analyze = st.button("🔍 Get Feedback", key="speaking_analyze", disabled=is_readonly)
        with cd:
            if st.button("🗑️ Delete", key="speaking_delete"):
                st.session_state.speaking_deleted = True
                st.session_state[f"mic_{topic_idx}"] = None
                st.rerun()
        st.success("✅ Recording saved! Click **Get Feedback** to analyse.")

        audio_bytes = audio_data.read()
        audio_data.seek(0)
        try:
            with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
                duration_seconds = wf.getnframes() / float(wf.getframerate())
        except Exception:
            duration_seconds = len(audio_bytes) / 32000

    if analyze and audio_data is not None:
        dur       = duration_seconds
        dur_score = min(9, max(4, int(4 + (dur/ideal)*5)))
        fluency   = min(9, dur_score)
        lexical   = 6
        grammar   = 6
        pronun    = 6
        overall   = round((fluency+lexical+grammar+pronun)/4, 1)
        st.session_state.scores["🗣️ Speaking"] = f"Band ~{overall}"

        st.markdown(f"""<div class="trophy-banner slide-up">
            <div class="trophy-icon">🎙️</div>
            <div class="trophy-text">
                <h2>Speaking Analysis</h2>
                <p>{topic['part']} · Duration: ~{int(dur)}s</p>
            </div>
            <div class="trophy-score">
                <div class="ts-label">Estimated Band</div>
                <div class="ts-value">{overall}</div>
            </div>
        </div>""", unsafe_allow_html=True)

        m1,m2,m3,m4 = st.columns(4)
        for col, lbl, sc in [(m1,f"Fluency ({int(dur)}s)",fluency),(m2,"Vocabulary*",lexical),(m3,"Grammar*",grammar),(m4,"Pronunciation*",pronun)]:
            with col: st.metric(lbl, sc)

        st.caption("*Vocabulary, Grammar, and Pronunciation are baseline estimates. For precise analysis, seek expert evaluation.")

        st.markdown("---")
        if dur < min_good:
            st.warning(f"🎙️ You spoke for ~{int(dur)} seconds. Aim for at least {min_good}–{ideal} seconds for this part!")
        elif dur >= ideal*0.8:
            st.success(f"🎙️ Excellent! You spoke for ~{int(dur)} seconds — great length for {part}!")
        else:
            st.success(f"🎙️ Good! You spoke for ~{int(dur)} seconds. Try to develop your answers a little more.")

        tips_speaking = [
            "🔤 **Pronunciation:** Record yourself and compare with native speakers. BBC Learning English is an excellent free resource.",
            "📚 **Vocabulary:** Use topic-specific words — avoid generic ones like 'good', 'bad', 'nice'. Try: 'beneficial', 'significant', 'remarkable'.",
            "🔄 **Fluency:** Don't stop after each point. Link ideas using: 'what's more', 'in addition to that', 'having said that'.",
            "⏱️ **Timing:** In Part 2, speak for the full 2 minutes. Use your preparation time to plan 3–4 bullet points.",
        ]
        for tip in tips_speaking:
            st.info(tip)

    st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style='background:{MOD_BG};border-radius:14px;padding:1.5rem 1.75rem;
                border-left:5px solid {ACCENT};border:1px solid {"#1c1c1c" if T=="dark" else "#2d1200"};
                border-left:5px solid {ACCENT};'>
        <h4 style='color:{ACCENT};margin:0 0 .4rem;font-family:Playfair Display,serif;'>🎓 Want a Real Speaking Evaluation?</h4>
        <p style='color:#9ca3af;margin:0 0 .9rem;font-size:.88rem;'>Get expert feedback from qualified IELTS instructors at IELTS Campus.</p>
        <a href='https://ieltscampus.com/courses/' target='_blank'
           style='background:{BTN_BG};color:white !important;padding:.55rem 1.25rem;border-radius:8px;
                  text-decoration:none !important;font-weight:700;font-size:.85rem;'>
            Book a Speaking Session →
        </a>
    </div>""", unsafe_allow_html=True)


# ─── FOOTER ───────────────────────────────────────────────────────────────────────
st.markdown(f"<div class='glow-divider'></div>", unsafe_allow_html=True)
st.markdown(f"""
<div style='text-align:center;color:{TEXT3};font-size:.82rem;padding:.75rem 0;'>
    ✈️ <strong style='color:{TEXT1};font-family:Playfair Display,serif;'>IELTS Campus</strong>
    &nbsp;·&nbsp; Free Practice Tests
    &nbsp;·&nbsp; <a href='https://ieltscampus.com' target='_blank' style='color:{ACCENT};font-weight:700;'>ieltscampus.com</a>
    &nbsp;·&nbsp; Powered by <span style='color:{ACCENT};'>Nxvel Studio</span>
</div>
""", unsafe_allow_html=True)

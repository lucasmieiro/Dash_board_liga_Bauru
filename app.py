# app.py — Mini terminal financeiro (USD/BRL, IBOV, SPY, BTC) + News + Agenda + Heatmap B3
# Auto-refresh a cada 25 minutos. Com "🔧 Debug IBOV" na sidebar.

import os, time
from datetime import datetime, date
from dateutil import tz
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

import streamlit as st

# ========== STREAMLIT BASE ==========
st.set_page_config(page_title="Mercado ao Vivo • Mini Terminal", page_icon="📈", layout="wide")
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=25 * 60 * 1000, key="refresh")  # 25 minutos
except Exception:
    pass

# ========== LIBS ==========
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests, feedparser, yfinance as yf

# ========== KEYS ==========
def get_secret(k, fallback=None):
    try:
        return st.secrets[k]
    except Exception:
        return os.getenv(k, fallback)

ALPHA_KEY   = get_secret("ALPHAVANTAGE_KEY")   # USD/BRL, SPY, BTC
NEWS_KEY    = get_secret("NEWSAPI_KEY")        # opcional
FMP_KEY     = get_secret("FMP_KEY")            # opcional (heatmap)
BRAPI_TOKEN = get_secret("BRAPI_TOKEN")        # opcional (IBOV/BOVA11 diário)

# ========== HTTP ==========
REQ = requests.Session()
REQ.headers.update({
    "User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept":"*/*",
    "Accept-Language":"pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection":"keep-alive",
})
TIMEOUT = 14

def has_net():
    try:
        REQ.get("https://www.google.com/generate_204", timeout=5)
        return True
    except Exception:
        return False

if not has_net():
    st.error("Sem internet/bloqueio de rede. O app abre, mas sem dados.")

# ========== CSS ==========
st.markdown("""
<style>
:root { --card-bg:#111418; }
body { background:#0c0f13; }
.block-container { padding-top: 1rem; }
h1,h2,h3 { color:#eaeef2; }
.news-card { background: var(--card-bg); border-radius: 12px; padding: .8rem 1rem; }
.big-headline { font-size: 1.05rem; font-weight: 700; color:#eaeef2; }
.small { font-size:.85rem; color:#b9c2cd; }
.tag { background:#1b2129; color:#d1dae3; padding:.15rem .45rem; border-radius:8px; font-size:.75rem; margin-right:.35rem;}
</style>
""", unsafe_allow_html=True)

# ========== HELPERS ==========
def line_chart(df, title, field="close", height=260):
    fig = go.Figure()
    if isinstance(df, pd.DataFrame) and not df.empty and field in df:
        fig.add_trace(go.Scatter(x=df.index, y=df[field], mode="lines", line=dict(width=2)))
    fig.update_layout(title=title, height=height, template="plotly_dark",
                      margin=dict(l=10, r=10, t=40, b=10), xaxis_title=None, yaxis_title=None)
    return fig

def last_price(df: pd.DataFrame, col="close"):
    return float(df[col].iloc[-1]) if isinstance(df, pd.DataFrame) and not df.empty and col in df else None

def _mask_url(url: str) -> str:
    """Remove apiKey/apikey/token dos query params para exibir no debug."""
    try:
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query))
        for k in list(q.keys()):
            if k.lower() in ("apikey", "api_key", "key", "token"):
                q[k] = "****"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    except Exception:
        return url

def _safe_get(url: str, params: dict = None, timeout: int = TIMEOUT):
    """Faz GET e retorna (resp, status, safe_url, text_excerpt)"""
    try:
        r = REQ.get(url, params=params, timeout=timeout)
        safe_url = _mask_url(r.url)
        txt = ""
        try:
            txt = r.text[:500]
        except Exception:
            txt = ""
        return r, r.status_code, safe_url, txt
    except Exception as e:
        try:
            req = requests.Request("GET", url, params=params).prepare()
            safe_url = _mask_url(req.url)
        except Exception:
            safe_url = url
        return None, None, safe_url, f"EXCEPTION: {e}"

# ========== ALPHA VANTAGE (TTL=25 min) ==========
@st.cache_data(ttl=1500)
def av_fx_intraday(from_symbol="USD", to_symbol="BRL", interval="5min"):
    if not ALPHA_KEY: return pd.DataFrame(), "no-key"
    try:
        r = REQ.get("https://www.alphavantage.co/query",
                    params={"function":"FX_INTRADAY","from_symbol":from_symbol,"to_symbol":to_symbol,
                            "interval":interval,"outputsize":"compact","apikey":ALPHA_KEY}, timeout=TIMEOUT)
        ts = r.json().get(f"Time Series FX ({interval})")
        if ts:
            df = pd.DataFrame(ts).T.sort_index(); df.index = pd.to_datetime(df.index)
            df["close"] = pd.to_numeric(df["4. close"], errors="coerce")
            df = df[["close"]].dropna()
            if not df.empty: return df, "intraday"
    except Exception: pass
    try:
        r = REQ.get("https://www.alphavantage.co/query",
                    params={"function":"FX_DAILY","from_symbol":from_symbol,"to_symbol":to_symbol,
                            "outputsize":"compact","apikey":ALPHA_KEY}, timeout=TIMEOUT)
        ts = r.json().get("Time Series FX (Daily)")
        if ts:
            df = pd.DataFrame(ts).T.sort_index(); df.index = pd.to_datetime(df.index)
            df["close"] = pd.to_numeric(df["4. close"], errors="coerce")
            return df[["close"]].dropna(), "daily"
    except Exception: pass
    return pd.DataFrame(), "empty"

@st.cache_data(ttl=1500)
def av_equity_intraday(symbol="SPY", interval="5min"):
    if not ALPHA_KEY: return pd.DataFrame()
    try:
        r = REQ.get("https://www.alphavantage.co/query",
                    params={"function":"TIME_SERIES_INTRADAY","symbol":symbol,"interval":interval,
                            "outputsize":"compact","apikey":ALPHA_KEY}, timeout=TIMEOUT)
        ts = r.json().get(f"Time Series ({interval})")
        if not ts: return pd.DataFrame()
        df = pd.DataFrame(ts).T.sort_index(); df.index = pd.to_datetime(df.index)
        df["close"] = pd.to_numeric(df["4. close"], errors="coerce")
        return df[["close"]].dropna()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=1500)
def av_crypto_series(symbol="BTC", market="USD", interval="5min"):
    if not ALPHA_KEY: return pd.DataFrame(), "no-key"
    try:
        r = REQ.get("https://www.alphavantage.co/query",
                    params={"function":"CRYPTO_INTRADAY","symbol":symbol,"market":market,
                            "interval":interval,"apikey":ALPHA_KEY}, timeout=TIMEOUT)
        ts = r.json().get(f"Time Series Crypto ({interval})")
        if ts:
            df = pd.DataFrame(ts).T.sort_index(); df.index = pd.to_datetime(df.index)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df[["close"]].dropna()
            if not df.empty: return df, "intraday"
    except Exception: pass
    try:
        r = REQ.get("https://www.alphavantage.co/query",
                    params={"function":"DIGITAL_CURRENCY_DAILY","symbol":symbol,"market":market,
                            "apikey":ALPHA_KEY}, timeout=TIMEOUT)
        ts = r.json().get("Time Series (Digital Currency Daily)")
        if ts:
            df = pd.DataFrame(ts).T.sort_index(); df.index = pd.to_datetime(df.index)
            df["close"] = pd.to_numeric(df["4b. close (USD)"], errors="coerce")
            return df[["close"]].dropna(), "daily"
    except Exception: pass
    return pd.DataFrame(), "empty"

@st.cache_data(ttl=1500)
def btc_series():
    df, src = av_crypto_series("BTC","USD","5min")
    if not df.empty: return df, f"Alpha Vantage ({src})"
    # fallback Binance sem chave
    try:
        data = REQ.get("https://api.binance.com/api/v3/klines",
                       params={"symbol":"BTCUSDT","interval":"5m","limit":300}, timeout=TIMEOUT).json()
        if isinstance(data, list) and data:
            rows = [{"dt": pd.to_datetime(k[6], unit="ms"), "close": float(k[4])} for k in data]
            df = pd.DataFrame(rows).set_index("dt").sort_index()
            return df, "Binance (USDT≈USD)"
    except Exception: pass
    return pd.DataFrame(), None

# ========== IBOV — Yahoo Chart v8 + yfinance + Stooq (.com/.pl) + brapi diário + proxy BOVA11 ==========
def _yahoo_chart(symbol: str, range_: str, interval: str):
    """Yahoo Chart v8 (query1 e query2) com region/lang BR."""
    hosts = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
    last_err = ("", None, "")
    for host in hosts:
        try:
            url = f"{host}/v8/finance/chart/{quote(symbol)}"
            params = {"range": range_, "interval": interval, "includePrePost": "false",
                      "region":"BR","lang":"pt-BR","useYfid":"true","events":"history"}
            r = REQ.get(url, params=params, timeout=TIMEOUT)
            safe_url = _mask_url(r.url)
            js = r.json()
            result = (js.get("chart", {}) or {}).get("result", [])
            if not result:
                last_err = (safe_url, r.status_code, (r.text or "")[:200]); continue
            res = result[0]
            ts = res.get("timestamp", []); quote_ = ((res.get("indicators", {}) or {}).get("quote", []) or [{}])[0]
            closes = quote_.get("close", [])
            if not ts or not closes:
                last_err = (safe_url, r.status_code, (r.text or "")[:200]); continue
            df = pd.DataFrame({"dt": pd.to_datetime(ts, unit="s"), "close": closes}).dropna()
            df = df.set_index("dt").sort_index()
            return df, safe_url, r.status_code, "ok"
        except Exception as e:
            last_err = (url, None, f"EXC: {e}"); continue
    return pd.DataFrame(), last_err[0], last_err[1], last_err[2]

def _stooq_ibov_daily():
    """Stooq (sem chave) — diário do IBOV: s=bvsp (tenta .com e .pl)."""
    for host in ["https://stooq.com", "https://stooq.pl"]:
        try:
            url = f"{host}/q/d/l/"
            params = {"s":"bvsp","i":"d"}
            r, status, safe_url, excerpt = _safe_get(url, params)
            if status != 200 or not r or not r.text:
                continue
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            if "Date" in df and "Close" in df:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.rename(columns={"Date":"dt","Close":"close"}).dropna()
                df = df[["dt","close"]].set_index("dt").sort_index()
                return df, status, safe_url, "ok"
        except Exception:
            continue
    return pd.DataFrame(), None, "", "sem resposta válida do Stooq"

def _brapi_daily_series(ticker: str):
    """brapi — somente diário típico do plano grátis: 1mo/1d e 3mo/1d."""
    combos = [("1mo","1d","brapi 1mo/1d"), ("3mo","1d","brapi 3mo/1d")]
    for rng,itv,label in combos:
        params = {"range": rng, "interval": itv}
        if BRAPI_TOKEN: params["token"] = BRAPI_TOKEN
        r, status, safe_url, excerpt = _safe_get("https://brapi.dev/api/quote/" + ticker, params)
        ok=False; rows=0
        try:
            data = r.json() if r is not None else {}
            res = (data.get("results") or [{}])[0]; candles = res.get("historicalDataPrice", [])
            rows = len(candles)
            if candles:
                df = pd.DataFrame(candles)
                df["date"] = pd.to_datetime(df["date"], unit="s")
                df = df.set_index("date").sort_index()
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df[["close"]].dropna(); ok = not df.empty
                if ok:
                    yield df, label, {"status":status,"url":safe_url,"note":str(excerpt)[:120]}
                    continue
        except Exception as e:
            excerpt = f"EXC json: {e}"
        yield None, label, {"status":status,"url":safe_url,"note":str(excerpt)[:120]}

def _normalize_to_level(proxy_df: pd.DataFrame, target_last: float | None):
    """Normaliza a série proxy (BOVA11) para o nível do alvo (se disponível)."""
    if proxy_df is None or proxy_df.empty:
        return proxy_df
    if target_last is None:
        return proxy_df
    pl = last_price(proxy_df)
    if pl is None or pl == 0:
        return proxy_df
    factor = target_last / pl
    out = proxy_df.copy()
    out["close"] = out["close"] * factor
    return out

def _ibov_series_impl(debug=False):
    logs = []

    # 1) Yahoo Chart v8 — testar várias combinações
    combos = [
        ("1d","1m","YahooChart 1d/1m"),
        ("1d","5m","YahooChart 1d/5m"),
        ("1d","15m","YahooChart 1d/15m"),
        ("5d","30m","YahooChart 5d/30m"),
        ("1mo","1d","YahooChart 1mo/1d"),
        ("3mo","1d","YahooChart 3mo/1d"),
        ("6mo","1d","YahooChart 6mo/1d"),
    ]
    for rng, itv, label in combos:
        df, safe_url, status, note = _yahoo_chart("^BVSP", rng, itv)
        ok = not df.empty
        logs.append({"provider":"YahooChart","step":label,"ok":ok,"rows":int(df.shape[0]) if ok else 0,
                     "status":status,"note":note,"url":safe_url})
        if ok: return df, label, logs

    # 2) yfinance (extra)
    for interval in ("1m","5m","15m","30m","60m"):
        try:
            ydf = yf.download("^BVSP", period="1d", interval=interval,
                              auto_adjust=True, progress=False, threads=False)
            rows = int(ydf.shape[0]) if isinstance(ydf, pd.DataFrame) else 0
            ok = bool(isinstance(ydf, pd.DataFrame) and not ydf.empty)
            logs.append({"provider":"YF","step":f"^BVSP intraday {interval}","ok":ok,"rows":rows,"status":"—","note":"","url":"yfinance"})
            if ok:
                ydf = ydf.rename(columns={"Close":"close"})[["close"]].dropna()
                if not ydf.empty: return ydf, f"Yahoo (yfinance) ^BVSP {interval}", logs
        except Exception as e:
            logs.append({"provider":"YF","step":f"^BVSP intraday {interval}","ok":False,"rows":0,"status":"EXC","note":str(e)[:140],"url":"yfinance"})
    for per in ("1mo","3mo"):
        try:
            ydf = yf.download("^BVSP", period=per, interval="1d",
                              auto_adjust=True, progress=False, threads=False)
            rows = int(ydf.shape[0]) if isinstance(ydf, pd.DataFrame) else 0
            ok = bool(isinstance(ydf, pd.DataFrame) and not ydf.empty)
            logs.append({"provider":"YF","step":f"^BVSP diário {per}","ok":ok,"rows":rows,"status":"—","note":"","url":"yfinance"})
            if ok:
                ydf = ydf.rename(columns={"Close":"close"})[["close"]].dropna()
                if not ydf.empty: return ydf, f"Yahoo (yfinance) ^BVSP {per}", logs
        except Exception as e:
            logs.append({"provider":"YF","step":f"^BVSP diário {per}","ok":False,"rows":0,"status":"EXC","note":str(e)[:140],"url":"yfinance"})

    # 3) Stooq (diário) — tenta .com e .pl
    df, status, safe_url, note = _stooq_ibov_daily()
    ok = not df.empty
    logs.append({"provider":"Stooq","step":"bvsp diário","ok":ok,"rows":int(df.shape[0]) if ok else 0,
                 "status":status,"note":note,"url":safe_url})
    if ok: return df, "Stooq diário (bvsp)", logs

    # 4) brapi — somente diário (1mo/1d e 3mo/1d)
    for result_df, label, meta in _brapi_daily_series("IBOV"):
        ok = isinstance(result_df, pd.DataFrame) and (result_df is not None) and not result_df.empty
        logs.append({"provider":"brapi","step":label,"ok":ok,"rows":int(result_df.shape[0]) if ok else 0,
                     "status":meta.get("status"),"note":meta.get("note"),"url":meta.get("url")})
        if ok: return result_df, label, logs

    # 5) Proxy BOVA11 — tenta achar qualquer coisa e normaliza ao último nível conhecido do Stooq (se houver)
    stooq_df, _, _, _ = _stooq_ibov_daily()
    stooq_last = last_price(stooq_df) if stooq_df is not None and not stooq_df.empty else None

    # YahooChart proxy
    for rng, itv, label in [("1d","1m","proxy BOVA11 YahooChart 1d/1m"),
                            ("1d","5m","proxy BOVA11 YahooChart 1d/5m"),
                            ("1d","15m","proxy BOVA11 YahooChart 1d/15m"),
                            ("5d","30m","proxy BOVA11 YahooChart 5d/30m"),
                            ("1mo","1d","proxy BOVA11 YahooChart 1mo/1d"),
                            ("3mo","1d","proxy BOVA11 YahooChart 3mo/1d")]:
        pdf, safe_url, status, note = _yahoo_chart("BOVA11.SA", rng, itv)
        ok = not pdf.empty
        logs.append({"provider":"YahooChart","step":label,"ok":ok,"rows":int(pdf.shape[0]) if ok else 0,
                     "status":status,"note":note,"url":safe_url})
        if ok:
            ndf = _normalize_to_level(pdf, stooq_last)
            return ndf, label + " (normalizado)", logs

    # Stooq proxy (BOVA11 diário)
    for host in ["https://stooq.com", "https://stooq.pl"]:
        try:
            url = f"{host}/q/d/l/"; params = {"s":"bova11.sa","i":"d"}
            r, status, safe_url, excerpt = _safe_get(url, params)
            if status == 200 and r and r.text:
                from io import StringIO
                pdf = pd.read_csv(StringIO(r.text))
                if "Date" in pdf and "Close" in pdf:
                    pdf["Date"] = pd.to_datetime(pdf["Date"])
                    pdf = pdf.rename(columns={"Date":"dt","Close":"close"}).dropna()[["dt","close"]].set_index("dt").sort_index()
                    ok = not pdf.empty
                    logs.append({"provider":"Stooq","step":"proxy BOVA11 diário","ok":ok,"rows":int(pdf.shape[0]) if ok else 0,
                                 "status":status,"note":"ok" if ok else "vazio","url":safe_url})
                    if ok:
                        ndf = _normalize_to_level(pdf, stooq_last)
                        return ndf, "proxy BOVA11 Stooq (normalizado)", logs
        except Exception as e:
            logs.append({"provider":"Stooq","step":"proxy BOVA11 diário","ok":False,"rows":0,"status":"EXC","note":str(e)[:140],"url":url})

    # 6) brapi proxy — diário
    for result_df, label, meta in _brapi_daily_series("BOVA11"):
        ok = isinstance(result_df, pd.DataFrame) and (result_df is not None) and not result_df.empty
        logs.append({"provider":"brapi","step":f"proxy {label}","ok":ok,"rows":int(result_df.shape[0]) if ok else 0,
                     "status":meta.get("status"),"note":meta.get("note"),"url":meta.get("url")})
        if ok:
            ndf = _normalize_to_level(result_df, stooq_last)
            return ndf, f"proxy {label} (normalizado)", logs

    # Nada funcionou
    return pd.DataFrame(), None, logs

@st.cache_data(ttl=1500)
def ibov_series_cached():
    df, src, _ = _ibov_series_impl(False)
    return df, src

def ibov_series(debug=False, force=False):
    if debug or force:
        return _ibov_series_impl(debug=True)
    else:
        df, src = ibov_series_cached()
        return df, src, []

# ========== HEATMAP B3 (FMP) ==========
FMP_BASE = "https://financialmodelingprep.com/api/v3"
B3_SECTORS = {
    "PETR4.SA":"Energia","PRIO3.SA":"Energia",
    "VALE3.SA":"Materiais","SUZB3.SA":"Materiais","KLBN11.SA":"Materiais","GGBR4.SA":"Materiais",
    "WEGE3.SA":"Industriais","EMBR3.SA":"Industriais","RAIL3.SA":"Industriais","CCRO3.SA":"Industriais",
    "B3SA3.SA":"Financeiro","ITUB4.SA":"Financeiro","BBDC4.SA":"Financeiro","BBAS3.SA":"Financeiro",
    "ABEV3.SA":"Consumo","LREN3.SA":"Consumo","MGLU3.SA":"Consumo",
    "ELET3.SA":"Utilidade","EQTL3.SA":"Utilidade",
}

@st.cache_data(ttl=1500)
def fmp_quotes(symbols):
    if not FMP_KEY: return []
    try:
        return REQ.get(f"{FMP_BASE}/quote/{','.join(symbols)}",
                       params={"apikey":FMP_KEY}, timeout=TIMEOUT).json()
    except Exception: return []

@st.cache_data(ttl=86400)
def fmp_profile(symbol):
    if not FMP_KEY: return {}
    try:
        arr = REQ.get(f"{FMP_BASE}/profile/{symbol}",
                      params={"apikey":FMP_KEY}, timeout=TIMEOUT).json()
        return arr[0] if isinstance(arr,list) and arr else {}
    except Exception: return {}

def build_b3_heatmap_df():
    if not FMP_KEY: return pd.DataFrame()
    syms = list(B3_SECTORS.keys()); qts = fmp_quotes(syms); qmap = {q.get("symbol"):q for q in qts if isinstance(q,dict)}
    rows=[]
    for s in syms:
        q=qmap.get(s,{}); pct=q.get("changesPercentage"); mcap=q.get("marketCap")
        if mcap in (None,0):
            prof=fmp_profile(s); mcap = prof.get("mktCap") or prof.get("marketCap") or 1.0
        rows.append({"ticker":s,"setor":B3_SECTORS[s],
                     "pct": float(pct) if isinstance(pct,(int,float)) else 0.0,
                     "mcap": float(mcap) if isinstance(mcap,(int,float)) and mcap>0 else 1.0})
        time.sleep(0.05)
    return pd.DataFrame(rows)

# ========== NOTÍCIAS ==========
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.infomoney.com.br/feed/",
    "https://economia.uol.com.br/ultimas/index.xml",
]

@st.cache_data(ttl=1500)
def get_rss_news(max_items=12):
    items = []
    for feed in RSS_FEEDS:
        try:
            f = feedparser.parse(feed, request_headers=REQ.headers)
            for e in f.entries[:max_items]:
                items.append({"title": e.get("title",""), "link": e.get("link",""), "source": f.feed.get("title","RSS")})
        except Exception:
            continue
    return items[:max_items]

@st.cache_data(ttl=1500)
def get_newsapi_news(max_items=10):
    if not NEWS_KEY: return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"category":"business","language":"en","pageSize":max_items,"apiKey":NEWS_KEY}
    try:
        r = REQ.get(url, params=params, timeout=TIMEOUT); data = r.json()
        out=[]
        for a in data.get("articles", []):
            out.append({"title": a.get("title"), "link": a.get("url"), "source": a.get("source",{}).get("name","NewsAPI")})
        return out
    except Exception:
        return []

@st.cache_data(ttl=1500)
def get_calendar_today(countries=("United States","Brazil")):
    try:
        r = REQ.get("https://api.tradingeconomics.com/calendar",
                    params={"c":"guest:guest","format":"json","d1":date.today().isoformat(),
                            "d2":date.today().isoformat(),"importance":"2,3"}, timeout=TIMEOUT)
        data = r.json(); df = pd.DataFrame(data)
        if df.empty: return pd.DataFrame()
        if countries: df = df[df["Country"].isin(countries)]
        cols = [c for c in ["Date","Country","Category","Event","Actual","Previous","Forecast"] if c in df.columns]
        df = df[cols].copy()
        try:
            local_tz = tz.tzlocal()
            df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.tz_convert(local_tz).dt.strftime("%H:%M")
        except Exception: pass
        return df.rename(columns={"Date":"Hora"}).sort_values("Hora")
    except Exception:
        return pd.DataFrame()

# ========== SIDEBAR ==========
with st.sidebar:
    st.subheader("Chaves")
    st.caption(f"AlphaVantage: {'✅' if ALPHA_KEY else '—'}")
    st.caption(f"FMP: {'✅' if FMP_KEY else '—'}")
    st.caption(f"NewsAPI: {'✅' if NEWS_KEY else '—'}")
    st.caption(f"brapi token: {'✅' if BRAPI_TOKEN else '—'}")
    st.write("---")
    DEBUG_IBOV = st.checkbox("🔧 Debug IBOV", value=False)
    FORCE_IBOV = st.button("Recarregar IBOV agora")

# ========== LAYOUT ==========
colL, colC, colR = st.columns([1.6, 1.4, 1.2], gap="small")

# Notícias
with colL:
    st.markdown("### 📺 Tema do dia")
    news = get_newsapi_news() or get_rss_news()
    if news:
        top = news[0]
        st.markdown(f"""
<div class="news-card">
  <div class="big-headline">{top['title']}</div>
  <div class="small">{top.get('source','')}</div>
  <div style="margin-top:.35rem"><a href="{top['link']}" target="_blank">Ler matéria ↗</a></div>
</div>
""", unsafe_allow_html=True)

    st.markdown("#### 📰 Top News")
    for n in news[1:7]:
        st.markdown(
            f"""<div class="news-card"><span class="tag">{n.get('source','')}</span>
            <a href="{n['link']}" target="_blank">{n['title']}</a></div>""",
            unsafe_allow_html=True,
        )

# Dólar + Agenda + Heatmap
with colC:
    fx_df, fx_src = av_fx_intraday("USD","BRL","5min")
    title = "USD/BRL (5m, intraday)" if fx_src=="intraday" else ("USD/BRL (últimos dias)" if fx_src=="daily" else "USD/BRL")
    st.markdown(f"### 💵 {title}")
    if not fx_df.empty:
        st.plotly_chart(line_chart(fx_df, ""), use_container_width=True)
        px_last = last_price(fx_df); st.caption(f"Último: {px_last:,.4f}" if isinstance(px_last,(int,float)) else "—")
    else:
        st.warning("Sem dados do USD/BRL (limite AV ou sem chave).")

    st.markdown("### 📈 Agenda rápida")
    cal = get_calendar_today()
    if not cal.empty:
        st.dataframe(cal, use_container_width=True, hide_index=True)
    else:
        st.caption("Sem eventos relevantes agora (ou limite da API).")

    st.markdown("### 🇧🇷 Heatmap Setorial — B3")
    if FMP_KEY:
        df_b3 = build_b3_heatmap_df()
        if not df_b3.empty:
            fig_b3 = px.treemap(df_b3, path=["setor","ticker"], values="mcap", color="pct",
                                color_continuous_scale=[(0.0,"#f45b69"),(0.5,"#1b2129"),(1.0,"#3dcc91")],
                                color_continuous_midpoint=0)
            fig_b3.update_layout(template="plotly_dark", margin=dict(l=0,r=0,t=25,b=0), height=410)
            st.plotly_chart(fig_b3, use_container_width=True)
            st.caption("Fonte: FMP (market cap/variação).")
        else:
            st.info("Não foi possível montar o heatmap agora (tente novamente).")
    else:
        st.info("Para ver o heatmap, adicione FMP_KEY em .streamlit/secrets.toml.")

# IBOV + SPY + BTC
with colR:
    st.markdown("### 📊 Índices / Cripto")

    ibov_df, ibov_src, logs = ibov_series(debug=DEBUG_IBOV, force=FORCE_IBOV)
    if not ibov_df.empty:
        st.plotly_chart(line_chart(ibov_df, f"Ibovespa — {ibov_src}"), use_container_width=True)
        lp = last_price(ibov_df)
        if lp is not None: st.caption(f"Último: {lp:,.0f}")
    else:
        st.warning("Sem dados do Ibovespa agora.")

    if DEBUG_IBOV:
        with st.expander("🔍 Logs de diagnóstico do IBOV"):
            if logs:
                dbg = pd.DataFrame(logs)
                st.dataframe(dbg, use_container_width=True, hide_index=True)
            else:
                st.caption("Sem logs (modo cache). Clique em **Recarregar IBOV agora** para forçar um teste sem cache.")

    spy = av_equity_intraday("SPY","5min")
    if not spy.empty:
        st.plotly_chart(line_chart(spy, "S&P 500 (SPY, 5m)"), use_container_width=True)
        st.caption(f"Último: {last_price(spy):,.2f}")
    else:
        st.warning("Sem dados do S&P 500 (SPY) — talvez limite da Alpha Vantage.")

    btc_df, btc_src = btc_series()
    if not btc_df.empty:
        st.plotly_chart(line_chart(btc_df, f"Bitcoin — {btc_src}"), use_container_width=True)
        st.caption(f"Último: {last_price(btc_df):,.2f}")
    else:
        st.warning("Sem dados do Bitcoin no momento.")

st.write("---")

# Mais manchetes
st.markdown("#### 🗞️ Mais manchetes")
extra = (get_newsapi_news(10) or []) + (get_rss_news(10) or [])
cols = st.columns(2)
for i, n in enumerate(extra[:10]):
    with cols[i % 2]:
        st.markdown(
            f"""<div class="news-card"><span class="tag">{n.get('source','')}</span>
            <a href="{n['link']}" target="_blank">{n['title']}</a></div>""",
            unsafe_allow_html=True,
        )

# Rodapé
local_tz = tz.tzlocal()
now_local = datetime.now(local_tz).strftime("%d/%m/%Y %H:%M")
st.caption(
    f"Atualizado em {now_local} • Auto-refresh: 25 min • Dados: Alpha Vantage (USD/BRL, SPY, BTC c/ fallback Binance), "
    f"Yahoo Chart / yfinance / Stooq (.com/.pl) / brapi (diário 1mo/3mo) / proxy BOVA11 (normalizado) — IBOV, "
    f"FMP (Heatmap), TradingEconomics (agenda), NewsAPI + RSS (manchetes)."
)

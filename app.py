import os
import sys
import time
import json
import math
import requests
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta, timezone

# -----------------------------------------
# Utilities
# -----------------------------------------

def set_env_from_secrets():
    """Populate os.environ with Streamlit secrets (safe no-ops locally)."""
    try:
        os.environ.update({k: str(v) for k, v in st.secrets.items()})
    except Exception:
        pass

def maintenance_guard():
    """Stop the app if MAINTENANCE is 'on' or until RESUME_AT timestamp."""
    flag = os.environ.get("MAINTENANCE", "off").lower()
    if flag != "on":
        return
    resume_at = os.environ.get("RESUME_AT")
    if resume_at:
        try:
            target = datetime.fromisoformat(resume_at)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now >= target.astimezone(timezone.utc):
                return
        except Exception:
            pass
    st.info("🚧 App em manutenção temporária. Tente novamente mais tarde.")
    st.stop()

def safe_combo(period: str, interval: str):
    """Normalize invalid period/interval combinations for Yahoo Finance."""
    intraday = {"1m","2m","5m","15m","30m","60m","90m"}
    if interval in intraday and period not in {"7d","14d","1mo","30d","2mo","3mo"}:
        period = "30d"
    if interval == "15m" and period not in {"14d","30d"}:
        interval = "30m"
    return period, interval

def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    try:
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
    except Exception:
        pass
    return df

# -----------------------------------------
# Data fetchers (all cached)
# -----------------------------------------

@st.cache_data(ttl=30*60, show_spinner=False)
def yf_download(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf
    period, interval = safe_combo(period, interval)
    try:
        df = yf.download(symbol, period=period, interval=interval,
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = _strip_tz(df)
        if "Adj Close" in df.columns:
            df = df.rename(columns={"Adj Close": "Close"})
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_btc(period="6mo", interval="1d") -> pd.DataFrame:
    df = yf_download("BTC-USD", period="6mo", interval="1d")
    if not df.empty and len(df) >= 10:
        return df[["Close"]].copy()

    df = yf_download("BTC-USD", period="30d", interval="30m")
    if not df.empty and len(df) >= 10:
        return df[["Close"]].copy()

    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": 180, "interval": "daily"},
            timeout=12,
        )
        r.raise_for_status()
        arr = r.json().get("prices", [])
        if arr:
            df = pd.DataFrame(arr, columns=["ts","Close"])
            df["Date"] = pd.to_datetime(df["ts"], unit="ms")
            df = df.set_index("Date")[ ["Close"] ]
            return df
    except Exception:
        pass

    av_key = os.environ.get("ALPHAVANTAGE_KEY")
    if av_key:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "DIGITAL_CURRENCY_DAILY",
                    "symbol": "BTC",
                    "market": "USD",
                    "apikey": av_key,
                },
                timeout=12,
            )
            r.raise_for_status()
            ts = r.json().get("Time Series (Digital Currency Daily)", {})
            if ts:
                df = pd.DataFrame.from_dict(ts, orient="index")
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                col = [c for c in df.columns if "close" in c.lower() and "(usd)" in c.lower()]
                if col:
                    out = df.rename(columns={col[0]: "Close"})[["Close"]].astype(float)
                    return out
        except Exception:
            pass

    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_usdbrl(period="6mo", interval="1d") -> pd.DataFrame:
    df = yf_download("USDBRL=X", period=period, interval=interval)
    if not df.empty and len(df) >= 5:
        return df[["Close"]].copy()

    av_key = os.environ.get("ALPHAVANTAGE_KEY")
    if av_key:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "FX_DAILY",
                    "from_symbol": "USD",
                    "to_symbol": "BRL",
                    "outputsize": "compact",
                    "apikey": av_key,
                },
                timeout=12,
            )
            r.raise_for_status()
            ts = r.json().get("Time Series FX (Daily)", {})
            if ts:
                df = pd.DataFrame.from_dict(ts, orient="index").astype(float)
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                df = df.rename(columns={"4. close": "Close"})[["Close"]]
                return df
        except Exception:
            pass

    try:
        end = datetime.utcnow().date()
        start = end - timedelta(days=180)
        r = requests.get(
            "https://api.exchangerate.host/timeseries",
            params={"base": "USD", "symbols": "BRL",
                    "start_date": start.isoformat(), "end_date": end.isoformat()},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        rates = data.get("rates", {})
        if rates:
            items = sorted((pd.to_datetime(k), v.get("BRL")) for k, v in rates.items())
            df = pd.DataFrame(items, columns=["Date","Close"]).set_index("Date")
            return df
    except Exception:
        pass

    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_spy(period="6mo", interval="1d") -> pd.DataFrame:
    df = yf_download("SPY", period=period, interval=interval)
    if not df.empty and len(df) >= 5:
        return df[["Close"]].copy()
    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_ibov(period="6mo", interval="1d") -> pd.DataFrame:
    df = yf_download("^BVSP", period=period, interval=interval)
    if not df.empty and len(df) >= 5:
        return df[["Close"]].copy()

    df2 = yf_download("BOVA11.SA", period=period, interval=interval)
    if not df2.empty and len(df2) >= 5:
        df2 = df2[["Close"]].copy()
        df2.rename(columns={"Close": "Close (BOVA11 proxy)"}, inplace=True)
        return df2

    token = os.environ.get("BRAPI_TOKEN")
    try:
        url = "https://brapi.dev/api/quote/IBOV"
        params = {"range": "6mo", "interval": "1d"}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results and "historicalDataPrice" in results[0]:
            hist = results[0]["historicalDataPrice"]
            df = pd.DataFrame(hist)
            if "date" in df.columns:
                df["Date"] = pd.to_datetime(df["date"], unit="s")
                df = df.set_index("Date")
            close_col = "close" if "close" in df.columns else "adjClose"
            if close_col in df.columns:
                out = df.rename(columns={close_col: "Close"})[["Close"]].dropna()
                return out
    except Exception:
        pass

    return pd.DataFrame()

@st.cache_data(ttl=20*60, show_spinner=False)
def get_news(limit=6):
    key = os.environ.get("NEWSAPI_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"language": "pt", "category": "business", "pageSize": limit},
            headers={"X-Api-Key": key},
            timeout=12,
        )
        r.raise_for_status()
        arts = r.json().get("articles", [])
        out = []
        for a in arts[:limit]:
            out.append({
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": (a.get("source") or {}).get("name", ""),
                "publishedAt": a.get("publishedAt", ""),
                "description": a.get("description", ""),
            })
        return out
    except Exception:
        return []

# -----------------------------------------
# UI
# -----------------------------------------

def render_chart(title: str, df: pd.DataFrame, unit: str = ""):
    st.subheader(title)
    if df is None or df.empty:
        st.error("Sem dados disponíveis no momento.")
        return
    st.caption(f"Registros: {len(df)} • {df.index.min().date()} → {df.index.max().date()}")
    st.line_chart(df["Close"])
    last = df["Close"].iloc[-1]
    st.metric("Último", f"{last:,.2f}{unit}".replace(",", "X").replace(".", ",").replace("X", "."))

def main():
    st.set_page_config(page_title="Dashboard Liga Bauru", layout="wide")
    set_env_from_secrets()
    maintenance_guard()

    st.title("📈 Dashboard — Liga Bauru")
    st.caption("USD/BRL, IBOV, SPY e BTC com fallbacks e timeouts.")

    st.sidebar.header("Parâmetros")
    period = st.sidebar.selectbox(
        "Período",
        ["7d", "14d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"],
        index=4,
    )
    interval = st.sidebar.selectbox(
        "Intervalo",
        ["1d", "1wk", "1mo", "30m", "15m"],
        index=0,
        help="Intraday (15m/30m) só funciona com janela curta (~30-60 dias)."
    )
    period, interval = safe_combo(period, interval)

    try:
        from streamlit_autorefresh import st_autorefresh
        refresh_minutes = st.sidebar.slider("Auto atualizar (min)", 0, 60, 0, help="0 = desativado")
        if refresh_minutes > 0:
            st_autorefresh(interval=refresh_minutes*60*1000, key="autorf")
    except Exception:
        pass

    tabs = st.tabs(["Mercados", "Notícias", "Status"])

    with tabs[0]:
        c1, c2 = st.columns(2)
        with c1:
            df_usdbrl = get_usdbrl(period, interval)
            render_chart("USD/BRL", df_usdbrl, unit=" R$")
        with c2:
            df_ibov = get_ibov(period, interval)
            render_chart("IBOV (ou BOVA11 proxy)", df_ibov)

        c3, c4 = st.columns(2)
        with c3:
            df_spy = get_spy(period, interval)
            render_chart("SPY (S&P 500 ETF)", df_spy, unit=" $")
        with c4:
            df_btc = get_btc(period, interval)
            render_chart("Bitcoin (USD)", df_btc, unit=" $")

    with tabs[1]:
        st.subheader("📰 Manchetes (NewsAPI)")
        news = get_news(limit=6)
        if not news:
            st.info("Sem notícias (configure NEWSAPI_KEY nos Secrets para ativar).")
        else:
            for n in news:
                with st.container(border=True):
                    st.markdown(f"**[{n['title']}]({n['url']})**")
                    colA, colB = st.columns([3,1])
                    with colA:
                        if n["description"]:
                            st.write(n["description"])
                    with colB:
                        st.caption(f"{n['source']}")
                        st.caption(n["publishedAt"])

    with tabs[2]:
        st.subheader("🔧 Status")
        env_ok = {k: ("✅" if os.environ.get(k) else "—") for k in ["ALPHAVANTAGE_KEY","FMP_KEY","NEWSAPI_KEY","BRAPI_TOKEN"]}
        st.write("Secrets:", env_ok)
        st.write("Versões:")
        try:
            import yfinance as yf
            st.write("yfinance:", yf.__version__)
        except Exception as e:
            st.write("yfinance não importou:", e)

        st.caption("Dica: se um provedor falhar, o app tenta o próximo. Sempre usamos timeout para evitar travar o carregamento.")

if __name__ == "__main__":
    main()

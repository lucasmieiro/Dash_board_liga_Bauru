# -*- coding: utf-8 -*-
# Streamlit Finance Mini-Terminal — patched (token saver, BTC fixes, no Yahoo)

import os
import sys
import time
from datetime import datetime

import pandas as pd
import numpy as np
import requests

import streamlit as st
from dateutil import tz
import plotly.graph_objects as go

# =====================
# CONFIG & GLOBALS
# =====================
st.set_page_config(page_title="Terminal Financeiro", layout="wide")

ALPHAVANTAGE_KEY = st.secrets.get("ALPHAVANTAGE_KEY") if hasattr(st, "secrets") else os.getenv("ALPHAVANTAGE_KEY")
BRAPI_TOKEN = st.secrets.get("BRAPI_TOKEN") if hasattr(st, "secrets") else os.getenv("BRAPI_TOKEN")

TIMEOUT = 12
SESSION = requests.Session()

# =====================
# HELPERS
# =====================

def get_now_brt():
    return datetime.now(tz.gettz("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S %Z")


def has_net():
    try:
        r = SESSION.get("https://www.google.com/generate_204", timeout=5)
        return r.status_code in (204, 200)
    except Exception:
        return False


def _safe_get(url, params=None, headers=None):
    try:
        r = SESSION.get(url, params=params, headers=headers, timeout=TIMEOUT)
        status = r.status_code
        text = r.text[:200]
        return r, status, r.url, text
    except Exception as e:
        return None, None, url, str(e)[:200]


def last_price(df: pd.DataFrame):
    try:
        return float(df.dropna().iloc[-1]["close"]) if isinstance(df, pd.DataFrame) and not df.empty else None
    except Exception:
        return None


def line_chart(df: pd.DataFrame, title: str):
    df = df.copy()
    df = df.sort_index()
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(go.Scatter(x=df.index, y=df["close"], mode="lines"))
    fig.update_layout(
        title=title,
        margin=dict(l=10, r=10, t=35, b=10),
        height=320,
        xaxis_title=None,
        yaxis_title=None,
        showlegend=False,
    )
    # quebra fins de semana
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def fmt_brl(x):  # 5400.3 -> 'R$ 5.400,30'
    if x is None:
        return "-"
    s = f"R$ {x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_usd(x):  # 1234.5 -> 'US$ 1.234,50'
    if x is None:
        return "-"
    s = f"US$ {x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# =====================
# TOKEN SAVER (19:00–07:00 BRT)
# =====================

def token_saver_active(now=None):
    tz_sp = tz.gettz("America/Sao_Paulo")
    now = now or datetime.now(tz_sp)
    return (now.hour >= 19) or (now.hour < 7)


if "TOKEN_SAVER" not in st.session_state:
    st.session_state["TOKEN_SAVER"] = token_saver_active()


def fetch_or_cache(cache_key, fn, *args, **kwargs):
    """
    Se o modo economia estiver ativo, retorna o último valor do cache local (session_state),
    sem chamar a API. Caso contrário, executa, guarda em cache e retorna.
    """
    if st.session_state.get("TOKEN_SAVER"):
        return st.session_state.get(cache_key, None)
    res = fn(*args, **kwargs)
    st.session_state[cache_key] = res
    return res


# =====================
# DATA PROVIDERS
# =====================

# --- Alpha Vantage: FX intraday/daily
@st.cache_data(ttl=1500)
def av_fx_intraday(base="USD", quote="BRL", interval="5min"):
    if not ALPHAVANTAGE_KEY:
        return pd.DataFrame(), None
    # Try intraday
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": base,
        "to_symbol": quote,
        "interval": interval,
        "apikey": ALPHAVANTAGE_KEY,
        "outputsize": "compact",
    }
    r, status, url, peek = _safe_get("https://www.alphavantage.co/query", params)
    try:
        if r is not None:
            js = r.json()
            # intraday key name varies by interval
            key = f"Time Series FX ({interval})"
            ts = js.get(key, {})
            if ts:
                df = pd.DataFrame(ts).T
                df.index = pd.to_datetime(df.index)
                df = df.rename(columns={"4. close": "close"})[["close"]]
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df.dropna().sort_index()
                if not df.empty:
                    return df, "Alpha Vantage (intraday)"
            # fallback FX_DAILY
            if not ts:
                params_daily = {
                    "function": "FX_DAILY",
                    "from_symbol": base,
                    "to_symbol": quote,
                    "apikey": ALPHAVANTAGE_KEY,
                    "outputsize": "compact",
                }
                r2, _, _, _ = _safe_get("https://www.alphavantage.co/query", params_daily)
                if r2 is not None:
                    js2 = r2.json()
                    ts2 = js2.get("Time Series FX (Daily)", {})
                    if ts2:
                        df = pd.DataFrame(ts2).T
                        df.index = pd.to_datetime(df.index)
                        df = df.rename(columns={"4. close": "close"})[["close"]]
                        df["close"] = pd.to_numeric(df["close"], errors="coerce")
                        df = df.dropna().sort_index()
                        if not df.empty:
                            return df, "Alpha Vantage (diário)"
    except Exception:
        pass
    return pd.DataFrame(), None


# --- exchangerate.host fallback para USD/BRL
@st.cache_data(ttl=900)
def fx_exchangerate_host(base="USD", quote="BRL"):
    try:
        r = SESSION.get(
            "https://api.exchangerate.host/latest",
            params={"base": base, "symbols": quote},
            timeout=TIMEOUT,
        ).json()
        rate = (r or {}).get("rates", {}).get(quote)
        if rate:
            idx = pd.date_range(end=pd.Timestamp.now(), periods=2, freq="T")
            df = pd.DataFrame({"close": [rate, rate]}, index=idx)
            return df, "exchangerate.host"
    except Exception:
        pass
    return pd.DataFrame(), None


# --- USD/BRL unified
@st.cache_data(ttl=900)
def usdbrl_series():
    df, src = av_fx_intraday("USD", "BRL", "5min")
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df, src
    er_df, er_src = fx_exchangerate_host("USD", "BRL")
    if isinstance(er_df, pd.DataFrame) and not er_df.empty:
        return er_df, er_src
    return pd.DataFrame(), None


# --- Alpha Vantage: CRYPTO (daily; intraday público é limitado)
@st.cache_data(ttl=1500)
def av_crypto_series(symbol="BTC", market="USD"):
    if not ALPHAVANTAGE_KEY:
        return pd.DataFrame(), None
    params = {
        "function": "DIGITAL_CURRENCY_DAILY",
        "symbol": symbol,
        "market": market,
        "apikey": ALPHAVANTAGE_KEY,
    }
    r, status, url, peek = _safe_get("https://www.alphavantage.co/query", params)
    try:
        if r is not None:
            js = r.json()
            ts = js.get("Time Series (Digital Currency Daily)", {})
            if ts:
                df = pd.DataFrame(ts).T
                df.index = pd.to_datetime(df.index)
                # "4b. close (USD)" costuma existir; senão "4a. close (USD)"
                col = "4b. close (USD)" if "4b. close (USD)" in df.columns else "4a. close (USD)"
                df = df.rename(columns={col: "close"})[["close"]]
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df.dropna().sort_index()
                if not df.empty:
                    return df, "Alpha Vantage (daily)"
    except Exception:
        pass
    return pd.DataFrame(), None


# --- BTC unified: AV -> Binance -> Coinbase
@st.cache_data(ttl=1500)
def btc_series():
    # 1) Alpha Vantage
    try:
        df, src = av_crypto_series("BTC", "USD")
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df, src
    except Exception:
        pass

    # 2) Binance (USDT≈USD, 5m)
    try:
        data = SESSION.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 300},
            timeout=TIMEOUT,
        ).json()
        if isinstance(data, list) and data:
            rows = [{"dt": pd.to_datetime(k[6], unit="ms"), "close": float(k[4])} for k in data]
            df = pd.DataFrame(rows).set_index("dt").sort_index()
            if not df.empty:
                return df, "Binance (USDT≈USD, 5m)"
    except Exception:
        pass

    # 3) Coinbase (BTC-USD, 5min)
    try:
        c = SESSION.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params={"granularity": 300},
            timeout=TIMEOUT,
        ).json()
        if isinstance(c, list) and c:
            rows = [{"dt": pd.to_datetime(row[0], unit="s"), "close": float(row[4])} for row in c]
            df = pd.DataFrame(rows).set_index("dt").sort_index()
            if not df.empty:
                return df, "Coinbase (BTC-USD, 5m)"
    except Exception:
        pass

    return pd.DataFrame(), None


# --- BCB/SGS (Selic 432)
@st.cache_data(ttl=6 * 3600)
def sgs_series(codigo=432):
    try:
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
        r = SESSION.get(url, params={"formato": "json"}, timeout=TIMEOUT)
        js = r.json()
        df = pd.DataFrame(js)
        df["data"] = pd.to_datetime(df["data"], dayfirst=True)
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df = df.dropna().set_index("data").sort_index()
        df = df.rename(columns={"valor": "close"})
        return df
    except Exception:
        return pd.DataFrame()


# --- IBOV (sem Yahoo/yfinance): Stooq -> brapi -> proxy BOVA11
@st.cache_data(ttl=1500)
def _stooq_csv_ibov(host="stooq.com"):
    urls = {
        "stooq.com": "https://stooq.com/q/d/l/?s=^bvsp&i=d",
        "stooq.pl": "https://stooq.pl/q/d/l/?s=^bvsp&i=d",
    }
    url = urls.get(host)
    r, status, safe_url, excerpt = _safe_get(url)
    try:
        if r is not None and r.status_code == 200 and "Date,Open,High,Low,Close,Volume" in r.text[:80]:
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            if "Date" in df.columns and "Close" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.rename(columns={"Date": "dt", "Close": "close"}).dropna()
                return df.set_index("dt").sort_index(), f"Stooq ({host})"
    except Exception:
        pass
    return pd.DataFrame(), None


@st.cache_data(ttl=1500)
def _brapi_daily_series_ibov():
    for rng, itv, label in [("1mo", "1d", "brapi 1mo/1d"), ("3mo", "1d", "brapi 3mo/1d")]:
        params = {"range": rng, "interval": itv}
        if BRAPI_TOKEN:
            params["token"] = BRAPI_TOKEN
        r, status, safe_url, excerpt = _safe_get("https://brapi.dev/api/quote/^BVSP", params)
        try:
            data = r.json() if r is not None else {}
            res = (data.get("results") or [{}])[0]
            candles = res.get("historicalDataPrice", [])
            if candles:
                df = pd.DataFrame(candles)
                df["date"] = pd.to_datetime(df["date"], unit="s")
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                df = df.set_index("date").sort_index()[["close"]].dropna()
                if not df.empty:
                    return df, label
        except Exception:
            pass
    return pd.DataFrame(), None


@st.cache_data(ttl=1500)
def _bova11_proxy():
    # brapi
    try:
        params = {"range": "3mo", "interval": "1d"}
        if BRAPI_TOKEN:
            params["token"] = BRAPI_TOKEN
        r, status, safe_url, excerpt = _safe_get("https://brapi.dev/api/quote/BOVA11", params)
        data = r.json() if r is not None else {}
        res = (data.get("results") or [{}])[0]
        candles = res.get("historicalDataPrice", [])
        if candles:
            df = pd.DataFrame(candles)
            df["date"] = pd.to_datetime(df["date"], unit="s")
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.set_index("date").sort_index()[["close"]].dropna()
            if not df.empty:
                ndf = (df / df.iloc[0, 0]) * 100000
                return ndf, "proxy BOVA11 (brapi normalizado)"
    except Exception:
        pass
    # Stooq do BOVA11 (fallback)
    try:
        r, status, safe_url, excerpt = _safe_get("https://stooq.com/q/d/l/?s=bova11&i=d")
        if r is not None and r.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            if "Date" in df.columns and "Close" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.rename(columns={"Date": "dt", "Close": "close"}).dropna().set_index("dt").sort_index()
                ndf = (df / df.iloc[0, 0]) * 100000
                return ndf, "proxy BOVA11 (Stooq normalizado)"
    except Exception:
        pass
    return pd.DataFrame(), None


def _ibov_series_impl():
    # 1) Stooq (.com → .pl)
    for host in ["stooq.com", "stooq.pl"]:
        df, src = _stooq_csv_ibov(host)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df, src
    # 2) brapi diário
    df, src = _brapi_daily_series_ibov()
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df, src
    # 3) proxy via BOVA11
    df, src = _bova11_proxy()
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df, src
    return pd.DataFrame(), None


@st.cache_data(ttl=1500)
def ibov_series():
    return _ibov_series_impl()


# =====================
# LAYOUT
# =====================

st.title("📊 Terminal Financeiro (mini)")

if not has_net():
    st.error("Sem conexão com a internet.")
    st.stop()

if st.session_state.get("TOKEN_SAVER"):
    st.info("🛑 Modo economia de tokens ativo (19:00–07:00 BRT). Mostrando último dado em cache; sem novas chamadas de API.")

colA, colB = st.columns([1, 1])
colC, colD = st.columns([1, 1])

# --- IBOV ---
with colA:
    st.subheader("IBOV — Série diária")
    ibov_res = fetch_or_cache("IBOV_SERIES", ibov_series)
    if ibov_res:
        ibov_df, ibov_src = ibov_res
    else:
        ibov_df, ibov_src = pd.DataFrame(), None

    if isinstance(ibov_df, pd.DataFrame) and not ibov_df.empty:
        st.plotly_chart(line_chart(ibov_df, f"IBOV — {ibov_src}"), use_container_width=True)
        st.caption(f"Último: {last_price(ibov_df):,.0f}")
    else:
        st.warning("Sem dados do IBOV agora.")

# --- Selic ---
with colB:
    st.subheader("🏦 Selic (meta BCB)")
    selic_res = fetch_or_cache("SELIC_SERIES", sgs_series, 432)
    if selic_res is None:
        selic_df = pd.DataFrame()
    else:
        selic_df = selic_res
    if isinstance(selic_df, pd.DataFrame) and not selic_df.empty:
        st.plotly_chart(line_chart(selic_df.tail(600), "Selic meta (últimos anos)"), use_container_width=True)
        st.caption(f"Último: {last_price(selic_df):.2f}% a.a.")
    else:
        st.warning("Não foi possível obter a Selic agora.")

# --- USD/BRL ---
with colC:
    st.subheader("USD/BRL")
    fx_res = fetch_or_cache("USD_BRL_SERIES", usdbrl_series)
    if fx_res:
        fx_df, fx_src = fx_res
    else:
        fx_df, fx_src = pd.DataFrame(), None

    if isinstance(fx_df, pd.DataFrame) and not fx_df.empty:
        st.plotly_chart(line_chart(fx_df, f"Câmbio USD/BRL — {fx_src}"), use_container_width=True)
        usdbrl = last_price(fx_df)
        st.markdown(f"**US$1 = {fmt_brl(usdbrl)}**")
        st.caption(f"Fonte: {fx_src}")
    else:
        st.warning("Sem dados de USD/BRL agora.")

# --- BTC ---
with colD:
    st.subheader("Bitcoin (BTC)")
    btc_res = fetch_or_cache("BTC_SERIES", btc_series)
    if btc_res:
        btc_df, btc_src = btc_res
    else:
        btc_df, btc_src = pd.DataFrame(), None

    if isinstance(btc_df, pd.DataFrame) and not btc_df.empty:
        st.plotly_chart(line_chart(btc_df, f"BTC — {btc_src}"), use_container_width=True)
        btc_usd = last_price(btc_df)
        fx_last = last_price(fx_df) if isinstance(fx_df, pd.DataFrame) and not fx_df.empty else None
        if fx_last is not None:
            st.markdown(f"**1 BTC = {fmt_usd(btc_usd)} • ≈ {fmt_brl(btc_usd * fx_last)}**")
        else:
            st.markdown(f"**1 BTC = {fmt_usd(btc_usd)}**")
        st.caption(f"Fonte: {btc_src}")
    else:
        st.warning("Sem dados de BTC agora.")

st.divider()

now_local = get_now_brt()
st.caption(
    f"Atualizado em {now_local} • Economia de tokens: {'ON' if st.session_state.get('TOKEN_SAVER') else 'OFF'} • "
    f"Dados: Alpha Vantage (USD/BRL, BTC diário), exchangerate.host (fallback USD/BRL), "
    f"BCB/SGS (Selic 432), Stooq (.com/.pl) / brapi (IBOV) / proxy BOVA11 (IBOV)."
)

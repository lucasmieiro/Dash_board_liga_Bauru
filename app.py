# Create a new single-page Streamlit app tailored to the user's requests.
import os, shutil, pathlib, zipfile, textwrap

root = pathlib.Path("/mnt/data/single_page_streamlit_app")
if root.exists():
    shutil.rmtree(root)
(root / ".streamlit").mkdir(parents=True, exist_ok=True)

app_py = """
import os
import requests
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta, timezone

# ============== Utilidades básicas ==============

def set_env_from_secrets():
    try:
        os.environ.update({k: str(v) for k, v in st.secrets.items()})
    except Exception:
        pass

def safe_combo(period: str, interval: str):
    \"\"\"Ajusta combinações inválidas para yfinance (intraday só com janela curta).\"\"\"
    intraday = {\"1m\",\"2m\",\"5m\",\"15m\",\"30m\",\"60m\",\"90m\"}
    if interval in intraday and period not in {\"7d\",\"14d\",\"1mo\",\"30d\",\"2mo\",\"3mo\"}:
        period = \"30d\"
    if interval == \"15m\" and period not in {\"14d\",\"30d\"}:
        interval = \"30m\"
    return period, interval

def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    try:
        if getattr(df.index, \"tz\", None) is not None:
            df.index = df.index.tz_localize(None)
    except Exception:
        pass
    return df

# ============== Buscadores de dados (com cache) ==============

@st.cache_data(ttl=30*60, show_spinner=False)
def yf_download(symbol: str, period: str = \"6mo\", interval: str = \"1d\") -> pd.DataFrame:
    import yfinance as yf
    p, i = safe_combo(period, interval)
    try:
        df = yf.download(symbol, period=p, interval=i, auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = _strip_tz(df)
        if \"Adj Close\" in df.columns:
            df = df.rename(columns={\"Adj Close\": \"Close\"})
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_btc(period=\"6mo\", interval=\"1d\") -> pd.DataFrame:
    # 1) yfinance diário (seguro p/ longos períodos)
    df = yf_download(\"BTC-USD\", period=\"6mo\", interval=\"1d\")
    if not df.empty and len(df) >= 10:
        return df[[\"Close\"]].copy()

    # 2) yfinance intraday (curto)
    df = yf_download(\"BTC-USD\", period=\"30d\", interval=\"30m\")
    if not df.empty and len(df) >= 10:
        return df[[\"Close\"]].copy()

    # 3) Coingecko diário
    try:
        r = requests.get(
            \"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart\",
            params={\"vs_currency\": \"usd\", \"days\": 180, \"interval\": \"daily\"},
            timeout=12,
        )
        r.raise_for_status()
        arr = r.json().get(\"prices\", [])
        if arr:
            df = pd.DataFrame(arr, columns=[\"ts\",\"Close\"])
            df[\"Date\"] = pd.to_datetime(df[\"ts\"], unit=\"ms\")
            return df.set_index(\"Date\")[ [\"Close\"] ]
    except Exception:
        pass

    # 4) Alpha Vantage diário (se houver key)
    key = os.environ.get(\"ALPHAVANTAGE_KEY\")
    if key:
        try:
            r = requests.get(
                \"https://www.alphavantage.co/query\",
                params={\"function\":\"DIGITAL_CURRENCY_DAILY\",\"symbol\":\"BTC\",\"market\":\"USD\",\"apikey\":key},
                timeout=12,
            )
            ts = r.json().get(\"Time Series (Digital Currency Daily)\", {})
            if ts:
                df = pd.DataFrame.from_dict(ts, orient=\"index\")
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                col = [c for c in df.columns if \"close\" in c.lower() and \"(usd)\" in c.lower()]
                if col:
                    return df.rename(columns={col[0]:\"Close\"})[[\"Close\"]].astype(float)
        except Exception:
            pass

    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_usdbrl(period=\"6mo\", interval=\"1d\") -> pd.DataFrame:
    # yfinance spot (seguro p/ vários períodos)
    df = yf_download(\"USDBRL=X\", period=period, interval=interval)
    if not df.empty and len(df) >= 5:
        return df[[\"Close\"]].copy()

    # Alpha Vantage FX_DAILY (sem intraday)
    key = os.environ.get(\"ALPHAVANTAGE_KEY\")
    if key:
        try:
            r = requests.get(
                \"https://www.alphavantage.co/query\",
                params={\"function\":\"FX_DAILY\",\"from_symbol\":\"USD\",\"to_symbol\":\"BRL\",\"outputsize\":\"compact\",\"apikey\":key},
                timeout=12,
            )
            ts = r.json().get(\"Time Series FX (Daily)\", {})
            if ts:
                df = pd.DataFrame.from_dict(ts, orient=\"index\").astype(float)
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                return df.rename(columns={\"4. close\":\"Close\"})[[\"Close\"]]
        except Exception:
            pass

    # exchangerate.host (diário, ~180 dias)
    try:
        end = datetime.utcnow().date()
        start = end - timedelta(days=180)
        r = requests.get(
            \"https://api.exchangerate.host/timeseries\",
            params={\"base\":\"USD\",\"symbols\":\"BRL\",\"start_date\":start.isoformat(),\"end_date\":end.isoformat()},
            timeout=12,
        )
        data = r.json().get(\"rates\", {})
        if data:
            items = sorted((pd.to_datetime(k), v.get(\"BRL\")) for k, v in data.items())
            return pd.DataFrame(items, columns=[\"Date\",\"Close\"]).set_index(\"Date\")
    except Exception:
        pass

    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_spy(period=\"6mo\", interval=\"1d\") -> pd.DataFrame:
    df = yf_download(\"SPY\", period=period, interval=interval)
    if not df.empty:
        return df[[\"Close\"]].copy()
    return pd.DataFrame()

@st.cache_data(ttl=30*60, show_spinner=False)
def get_ibov(period=\"6mo\", interval=\"1d\") -> pd.DataFrame:
    # 1) yfinance IBOV
    df = yf_download(\"^BVSP\", period=period, interval=interval)
    if not df.empty and len(df) >= 5:
        return df[[\"Close\"]].copy()

    # 2) BOVA11 proxy
    df2 = yf_download(\"BOVA11.SA\", period=period, interval=interval)
    if not df2.empty and len(df2) >= 5:
        return df2[[\"Close\"]].copy()

    # 3) brapi (diário)
    token = os.environ.get(\"BRAPI_TOKEN\")
    try:
        url = \"https://brapi.dev/api/quote/IBOV\"
        params = {\"range\":\"6mo\",\"interval\":\"1d\"}
        headers = {\"Authorization\": f\"Bearer {token}\"} if token else {}
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        results = r.json().get(\"results\", [])
        if results and \"historicalDataPrice\" in results[0]:
            hist = results[0][\"historicalDataPrice\"]
            df = pd.DataFrame(hist)
            if \"date\" in df.columns:
                df[\"Date\"] = pd.to_datetime(df[\"date\"], unit=\"s\")
                df = df.set_index(\"Date\")
            close_col = \"close\" if \"close\" in df.columns else \"adjClose\"
            if close_col in df.columns:
                return df.rename(columns={close_col:\"Close\"})[[\"Close\"]].dropna()
    except Exception:
        pass

    return pd.DataFrame()

@st.cache_data(ttl=20*60, show_spinner=False)
def get_news(limit=8):
    key = os.environ.get(\"NEWSAPI_KEY\")
    if not key:
        return []
    try:
        r = requests.get(
            \"https://newsapi.org/v2/top-headlines\",
            params={\"language\":\"pt\",\"category\":\"business\",\"pageSize\":limit},
            headers={\"X-Api-Key\": key},
            timeout=12,
        )
        r.raise_for_status()
        arts = r.json().get(\"articles\", [])
        out = []
        for a in arts[:limit]:
            out.append({
                \"title\": a.get(\"title\", \"\"),
                \"url\": a.get(\"url\", \"\"),
                \"source\": (a.get(\"source\") or {}).get(\"name\", \"\"),
                \"publishedAt\": a.get(\"publishedAt\", \"\"),
                \"description\": a.get(\"description\", \"\"),
            })
        return out
    except Exception:
        return []

@st.cache_data(ttl=20*60, show_spinner=False)
def get_b3_heatmap(limit=80):
    \"\"\"Heatmap via FMP (se disponível). Usa stock-screener da B3/SAO.\"\"\"
    key = os.environ.get(\"FMP_KEY\")
    if not key:
        return pd.DataFrame()
    try:
        r = requests.get(
            \"https://financialmodelingprep.com/api/v3/stock-screener\",
            params={\"exchange\":\"SAO\",\"limit\":limit,\"apikey\":key},
            timeout=12,
        )
        r.raise_for_status()
        arr = r.json()
        if not arr:
            return pd.DataFrame()
        df = pd.DataFrame(arr)
        # Normaliza colunas esperadas
        for col in [\"symbol\",\"companyName\",\"sector\",\"price\",\"changesPercentage\",\"marketCap\"]:
            if col not in df.columns:
                df[col] = np.nan
        df[\"sector\"] = df[\"sector\"].fillna(\"Sem setor\")
        # Garantir métricas numéricas
        for col in [\"price\",\"changesPercentage\",\"marketCap\"]:
            df[col] = pd.to_numeric(df[col], errors=\"coerce\")
        # Remove linhas sem símbolo
        df = df.dropna(subset=[\"symbol\"]).head(limit)
        return df
    except Exception:
        return pd.DataFrame()

# ============== UI ==============

def render_chart(title: str, df: pd.DataFrame, unit: str = \"\", height: int = 260):
    st.markdown(f\"### {title}\")
    if df is None or df.empty:
        st.error(\"Sem dados disponíveis.\")
        return
    st.caption(f\"Registros: {len(df)} • {df.index.min().date()} → {df.index.max().date()}\")
    st.line_chart(df[\"Close\"], height=height)
    last = float(df[\"Close\"].iloc[-1])
    st.caption(f\"Último: **{last:,.4f}{unit}**\".replace(\",\",\"X\").replace(\".\",\",\").replace(\"X\",\".\"))

def main():
    st.set_page_config(page_title=\"Dashboard Liga Bauru\", layout=\"wide\")
    set_env_from_secrets()

    st.title(\"📊 Dashboard — Liga Bauru (janela única)\")
    st.caption(\"Notícias à esquerda; gráficos e heatmap à direita. BTC/USDBRL mostram o último valor nas legendas.\")

    # Sidebar — parâmetros globais (cada fonte ajusta internamente o que suporta)
    st.sidebar.header(\"Parâmetros globais\")
    period = st.sidebar.selectbox(\"Período base\", [\"7d\",\"14d\",\"1mo\",\"3mo\",\"6mo\",\"1y\",\"2y\",\"5y\",\"max\"], index=4)
    interval = st.sidebar.selectbox(\"Intervalo base\", [\"1d\",\"1wk\",\"1mo\",\"30m\",\"15m\"], index=0)

    news_col, charts_col = st.columns([1, 2.6])

    # --------- Coluna de notícias ---------
    with news_col:
        st.subheader(\"📰 Notícias\")
        news = get_news(limit=8)
        if not news:
            st.info(\"Configure o `NEWSAPI_KEY` em Secrets para ativar as manchetes.\")
        else:
            for n in news:
                with st.container(border=True):
                    st.markdown(f\"**[{n['title']}]({n['url']})**\" if n[\"url\"] else f\"**{n['title']}**\")
                    if n[\"description\"]:
                        st.write(n[\"description\"])                
                    st.caption(f\"{n['source']} • {n['publishedAt']}\")

    # --------- Coluna de gráficos/heatmap ---------
    with charts_col:
        # Métricas de topo: último USD/BRL e último BTC
        cA, cB = st.columns(2)
        # USD/BRL
        usd_df = get_usdbrl(period, interval)
        last_usd = float(usd_df[\"Close\"].iloc[-1]) if not usd_df.empty else None
        cA.metric(\"USD/BRL (último)\", (f\"{last_usd:,.4f} R$\" if last_usd is not None else \"—\").replace(\",\",\"X\").replace(\".\",\",\").replace(\"X\",\".\"))
        # BTC
        btc_df = get_btc(period, interval)
        last_btc = float(btc_df[\"Close\"].iloc[-1]) if not btc_df.empty else None
        cB.metric(\"BTC/USD (último)\", (f\"{last_btc:,.2f} $\" if last_btc is not None else \"—\").replace(\",\",\"X\").replace(\".\",\",\").replace(\"X\",\".\"))

        # Linha 1: USD/BRL e BTC
        c1, c2 = st.columns(2)
        with c1:
            render_chart(\"USD/BRL\", usd_df, unit=\" R$\")
        with c2:
            render_chart(\"Bitcoin (USD)\", btc_df, unit=\" $\")

        # Linha 2: IBOV e SPY
        c3, c4 = st.columns(2)
        with c3:
            ibov_df = get_ibov(period, interval)
            render_chart(\"IBOV (ou BOVA11 proxy)\", ibov_df)
        with c4:
            spy_df = get_spy(period, interval)
            render_chart(\"SPY (S&P 500)\", spy_df, unit=\" $\")

        # Heatmap B3 (FMP)
        st.markdown(\"### Heatmap — B3 (via FMP)\")
        df_hm = get_b3_heatmap(limit=80)
        if df_hm.empty:
            st.info(\"Heatmap indisponível (requer `FMP_KEY` e endpoint acessível).\" )
        else:
            try:
                import plotly.express as px
                # Define coluna de tamanho
                size_col = \"marketCap\" if df_hm[\"marketCap\"].notna().any() else \"price\"
                fig = px.treemap(
                    df_hm,
                    path=[\"sector\",\"symbol\"],
                    values=size_col,
                    color=\"changesPercentage\",
                    hover_data=[\"companyName\",\"price\",\"changesPercentage\",\"marketCap\"],
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.warning(f\"Falha ao renderizar heatmap: {e}\")

    # Rodapé de status opcional
    with st.expander(\"Status & Secrets\"):
        env_ok = {k: (\"✅\" if os.environ.get(k) else \"—\") for k in [\"ALPHAVANTAGE_KEY\",\"FMP_KEY\",\"NEWSAPI_KEY\",\"BRAPI_TOKEN\"]}
        st.write(\"Secrets:\", env_ok)

if __name__ == \"__main__\":
    st.set_page_config(page_title=\"Dashboard Liga Bauru\", layout=\"wide\")
    main()
"""

requirements = """
streamlit>=1.34
pandas>=2.2
numpy>=1.26
requests>=2.31
yfinance>=0.2.40
plotly>=5.20
"""

readme = """
# Dashboard Liga Bauru — Janela Única

- Notícias à esquerda (NewsAPI)
- Gráficos à direita (USD/BRL, BTC, IBOV/BOVA11, SPY)
- Heatmap da B3 via FMP (opcional)
- Cada fonte ajusta automaticamente o período/intervalo para o que é suportado, evitando gráficos vazios.

## Secrets esperados
- `ALPHAVANTAGE_KEY` (opcional, FX/BTC daily)
- `FMP_KEY` (opcional, heatmap B3)
- `NEWSAPI_KEY` (opcional, manchetes)
- `BRAPI_TOKEN` (opcional, fallback IBOV)

## Observações
- Se usar intraday (15m/30m), janelas longas são reduzidas automaticamente (~30 dias).
- Recomendado: Python **3.11** no Streamlit Cloud.
"""

secrets_example = """
# .streamlit/secrets.example.toml
ALPHAVANTAGE_KEY = "your_alpha_vantage_key"
FMP_KEY          = "your_fmp_key"
NEWSAPI_KEY      = "your_newsapi_key"
BRAPI_TOKEN      = "your_brapi_token"
"""

# Write files
(root / "app.py").write_text(textwrap.dedent(app_py).strip() + "\n", encoding="utf-8")
(root / "requirements.txt").write_text(textwrap.dedent(requirements).strip() + "\n", encoding="utf-8")
(root / "README.md").write_text(textwrap.dedent(readme).strip() + "\n", encoding="utf-8")
(root / ".streamlit" / "secrets.example.toml").write_text(textwrap.dedent(secrets_example).strip() + "\n", encoding="utf-8")

# Zip it
zip_path = "/mnt/data/single_page_streamlit_app.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob("*"):
        z.write(p, p.relative_to(root.parent).as_posix())

zip_path

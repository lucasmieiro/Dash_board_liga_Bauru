# Mini Terminal Financeiro (Streamlit)

App Streamlit com USD/BRL, IBOV (com múltiplos fallbacks), SPY, BTC, manchetes e heatmap da B3.

## Deploy rápido (gratuito)

### Opção A — Streamlit Community Cloud (recomendado)
1. Crie um **repositório público no GitHub** com estes arquivos.
2. Vá em https://share.streamlit.io/ → **Deploy app** → conecte seu repo e escolha `app.py`.
3. Em **App settings → Secrets**, cole:
```
ALPHAVANTAGE_KEY="..."
FMP_KEY="..."           # opcional
NEWSAPI_KEY="..."       # opcional
BRAPI_TOKEN="..."       # opcional
```
4. Clique **Deploy**.

### Opção B — Hugging Face Spaces
1. New Space → Type **Streamlit** → Hardware **CPU Basic** (free).
2. Faça upload dos arquivos ou conecte ao GitHub.
3. Em **Settings → Variables**, adicione as mesmas chaves acima como **secrets**.

### Opção C — Render (free tier com hibernação)
- New → Web Service
- Build Command: `pip install -r requirements.txt`
- Start Command: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`
- Adicione as chaves em **Environment**.

## Notas
- **Não commit** `.streamlit/secrets.toml` com chaves reais.
- O heatmap da B3 só aparece se `FMP_KEY` estiver definido.
- O IBOV usa Yahoo Chart / yfinance / Stooq / brapi / proxy BOVA11 como fallbacks.
- Auto-refresh a cada 25 min (se `streamlit_autorefresh` não estiver instalado, segue sem esse recurso).

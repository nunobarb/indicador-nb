#!/usr/bin/env python3
"""
Indicador NB - Backend API
100% automático, sem actualizações manuais.

v2 — correcções:
  • Fear & Greed: CNN (acções) como fonte primária; alternative.me (crypto)
    apenas como proxy de último recurso, devidamente assinalado.
  • Buffett Indicator: usa BOGZ1LM893064105Q (corporate equities, todos os
    sectores, em milhões USD) ÷ GDP — a série DDDM01USA156NWDB era o próprio
    ratio (anual, descontinuada em 2020) e estava a ser dividida duas vezes.
  • Fetch paralelo dos 6 indicadores (ThreadPoolExecutor) — cold start ~6x
    mais rápido.
  • Lógica de alertas movida para o web service (/api/check-alerts) — o
    estado anti-spam vive na memória do processo de longa duração, em vez
    de /tmp num container de cron efémero.
  • Score calculado apenas aqui (single source of truth); frontend e
    alerter consomem o valor da API.
"""

from flask import Flask, jsonify, send_file
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
import requests
import os
import re

app = Flask(__name__)
CORS(app)

API_KEYS = {
    'fred': os.getenv('FRED_KEY', 'YOUR_FRED_KEY'),
}

FRED_BASE = 'https://api.stlouisfed.org/fred/series/observations'

_cache = {}
CACHE_DURATION = timedelta(hours=6)


def now_utc():
    return datetime.now(timezone.utc)


def get_cached(key, allow_stale=False):
    """allow_stale=True devolve o último valor conhecido mesmo expirado
    (melhor que um fallback hardcoded quando a fonte está em baixo)."""
    if key in _cache:
        data, timestamp = _cache[key]
        if allow_stale or now_utc() - timestamp < CACHE_DURATION:
            return data
    return None


def set_cached(key, data):
    _cache[key] = (data, now_utc())


def fred_observations(series_id, limit=5):
    """Devolve lista de floats válidos (mais recente primeiro) de uma série FRED."""
    url = (f'{FRED_BASE}?series_id={series_id}&api_key={API_KEYS["fred"]}'
           f'&file_type=json&limit={limit}&sort_order=desc')
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return [float(o['value']) for o in r.json()['observations'] if o['value'] != '.']


# ─────────────────────────────────────────────────────────────────────────────
# INDICADORES
# ─────────────────────────────────────────────────────────────────────────────

def get_fear_greed_index():
    cached = get_cached('fear_greed')
    if cached: return cached

    current, avg_30d, source = None, None, None

    # Fonte 1: CNN Fear & Greed (mercado de ACÇÕES — a fonte correcta)
    try:
        headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/124.0 Safari/537.36'),
            'Accept': 'application/json',
        }
        r = requests.get(
            'https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
            headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        current = float(data['fear_and_greed']['score'])
        hist = data.get('fear_and_greed_historical', {}).get('data', [])
        if hist:
            vals = [float(p['y']) for p in hist[-30:]]
            avg_30d = sum(vals) / len(vals)
        source = 'CNN'
    except Exception as e:
        print(f"Erro F&G CNN: {e}")

    # Fonte 2 (último recurso): alternative.me — Fear & Greed de CRYPTO.
    # Correlacionado mas NÃO é o sentimento de acções; assinalado como proxy.
    if current is None:
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=30", timeout=10)
            r.raise_for_status()
            data = r.json()
            current = float(data['data'][0]['value'])
            values = [float(d['value']) for d in data['data']]
            avg_30d = sum(values) / len(values)
            source = 'Crypto (proxy)'
        except Exception as e:
            print(f"Erro F&G alternative.me: {e}")

    if current is None:
        stale = get_cached('fear_greed', allow_stale=True)
        if stale: return stale
        return {'value': 50, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}

    current = round(current)
    result = {
        'value': current,
        'avg_30d': round(avg_30d, 1) if avg_30d is not None else None,
        'source': source,
        'signal': 'STRONG_BUY' if current < 20 else 'BUY' if current < 35 else
                  'SELL' if current > 75 else 'CAUTION' if current > 65 else 'NEUTRAL',
        'description': f'Actual: {current} ({source})' +
                       (f', Média 30d: {avg_30d:.0f}' if avg_30d is not None else '')
    }
    set_cached('fear_greed', result)
    print(f"Fear & Greed [{source}]: {current}")
    return result


def get_vix():
    """VIX via FRED — série oficial VIXCLS."""
    cached = get_cached('vix')
    if cached: return cached
    try:
        vix = fred_observations('VIXCLS', limit=5)[0]
        result = {
            'value': round(vix, 1),
            'signal': 'STRONG_BUY' if vix > 40 else 'BUY' if vix > 28 else
                      'SELL' if vix < 13 else 'CAUTION' if vix < 17 else 'NEUTRAL',
            'description': f'VIX: {vix:.1f}'
        }
        set_cached('vix', result)
        print(f"VIX: {vix}")
        return result
    except Exception as e:
        print(f"Erro VIX: {e}")
        stale = get_cached('vix', allow_stale=True)
        return stale or {'value': 18, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_sp500_vs_ma200():
    """S&P 500 via FRED (série SP500) — desvio vs média de 200 sessões."""
    cached = get_cached('sp500_ma200')
    if cached: return cached
    try:
        obs = fred_observations('SP500', limit=300)
        if len(obs) < 200:
            raise ValueError("Dados insuficientes para MA200")
        price = obs[0]
        ma200 = sum(obs[:200]) / 200
        pct = ((price / ma200) - 1) * 100
        result = {
            'value': round(pct, 1),
            'signal': 'STRONG_BUY' if pct < -15 else 'BUY' if pct < -5 else
                      'SELL' if pct > 15 else 'CAUTION' if pct > 10 else 'NEUTRAL',
            'description': f'S&P vs MA200: {pct:+.1f}% (${price:.0f})'
        }
        set_cached('sp500_ma200', result)
        print(f"S&P vs MA200: {pct:+.1f}%")
        return result
    except Exception as e:
        print(f"Erro S&P/MA200: {e}")
        stale = get_cached('sp500_ma200', allow_stale=True)
        return stale or {'value': 0, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_shiller_pe():
    cached = get_cached('shiller_pe')
    if cached: return cached
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; indicadornb/2.0)'}
        r = requests.get('https://www.multpl.com/shiller-pe',
                         headers=headers, timeout=10)
        r.raise_for_status()
        match = (re.search(r'<div id="current"[^>]*>\s*([\d.]+)', r.text) or
                 re.search(r'Current Shiller PE Ratio[^0-9]*([\d.]+)', r.text) or
                 re.search(r'Shiller PE Ratio[^<]*<[^>]+>([\d.]+)', r.text))
        if not match:
            raise ValueError("Padrão não encontrado no multpl.com")
        cape = float(match.group(1))
        if not (5 <= cape <= 60):  # sanity check anti-scraping-lixo
            raise ValueError(f"CAPE fora do range plausível: {cape}")
        result = {
            'value': round(cape, 1),
            'signal': 'SELL' if cape > 35 else 'CAUTION' if cape > 28 else
                      'STRONG_BUY' if cape < 14 else 'BUY' if cape < 18 else 'NEUTRAL',
            'description': f'Shiller P/E: {cape:.1f}'
        }
        set_cached('shiller_pe', result)
        print(f"Shiller P/E: {cape}")
        return result
    except Exception as e:
        print(f"Erro Shiller P/E: {e}")
        stale = get_cached('shiller_pe', allow_stale=True)
        return stale or {'value': 30.0, 'signal': 'CAUTION',
                         'description': 'Shiller P/E: indisponível (último conhecido ~30)'}


def get_buffett_indicator():
    """
    Buffett Indicator = Market Cap total / GDP nominal.

    Market cap: BOGZ1LM893064105Q — "All Sectors; Corporate Equities;
    Liability, Level" (Z.1 Flow of Funds, trimestral, em MILHÕES de USD).
    Fallback: NCBEILQ027S (só nonfinancial — subestima; assinalado).
    GDP: série GDP (trimestral, em MILES DE MILHÕES de USD).
    """
    cached = get_cached('buffett')
    if cached: return cached
    try:
        suffix = ''
        try:
            market_cap_mm = fred_observations('BOGZ1LM893064105Q', limit=5)[0]
        except Exception as e:
            print(f"Buffett: fallback NCBEILQ027S ({e})")
            market_cap_mm = fred_observations('NCBEILQ027S', limit=5)[0]
            suffix = ' (nonfin.)'

        gdp_bn = fred_observations('GDP', limit=5)[0]
        ratio = (market_cap_mm / 1000.0) / gdp_bn * 100  # milhões→mil milhões

        if not (30 <= ratio <= 400):  # sanity check contra erro de unidades
            raise ValueError(f"Ratio implausível: {ratio:.0f}%")

        result = {
            'value': round(ratio, 0),
            'signal': 'SELL' if ratio > 185 else 'CAUTION' if ratio > 165 else
                      'STRONG_BUY' if ratio < 80 else 'BUY' if ratio < 100 else 'NEUTRAL',
            'description': f'Market/GDP: {ratio:.0f}%{suffix}'
        }
        set_cached('buffett', result)
        print(f"Buffett Indicator: {ratio:.0f}%{suffix}")
        return result
    except Exception as e:
        print(f"Erro Buffett: {e}")
        stale = get_cached('buffett', allow_stale=True)
        return stale or {'value': 150, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_high_yield_spread():
    cached = get_cached('hy_spread')
    if cached: return cached
    try:
        spread = fred_observations('BAMLH0A0HYM2', limit=5)[0]
        result = {
            'value': round(spread, 2),
            'signal': 'STRONG_BUY' if spread > 8 else 'BUY' if spread > 5 else
                      'SELL' if spread < 3 else 'NEUTRAL',
            'description': f'HY Spread: {spread:.2f}%'
        }
        set_cached('hy_spread', result)
        return result
    except Exception as e:
        print(f"Erro HY Spread: {e}")
        stale = get_cached('hy_spread', allow_stale=True)
        return stale or {'value': 4, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


# ─────────────────────────────────────────────────────────────────────────────
# SCORES (única implementação — frontend e alerter consomem daqui)
# ─────────────────────────────────────────────────────────────────────────────

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def calculate_score(fg, vix, breadth, buffett, cape, hy):
    """
    Pesos rebalanceados para um indicador de TIMING contrarian.
    Sentimento + técnico = 65% (dizem QUANDO agir)
    Valuation = 35% (contexto estrutural)
    """
    s_fg  = fg
    s_vix = 100 - clamp(((vix - 10) / 45) * 100, 0, 100)
    s_buf = clamp(((buffett - 80) / 140) * 100, 0, 100)
    s_cap = clamp(((cape - 10) / 35) * 100, 0, 100)
    s_ma  = clamp(50 + (breadth / 30) * 50, 0, 100)
    s_hy  = 100 - clamp(((hy - 1.5) / 10.5) * 100, 0, 100)

    return round(
        s_fg  * 0.30 +   # Fear & Greed: 30% — sinal de timing primário
        s_vix * 0.20 +   # VIX:          20% — stress/pânico de mercado
        s_ma  * 0.15 +   # S&P vs MA200: 15% — posição técnica
        s_hy  * 0.15 +   # HY Spread:    15% — stress de crédito
        s_buf * 0.10 +   # Buffett:      10% — contexto valuation
        s_cap * 0.10     # CAPE:         10% — contexto histórico
    )


def calculate_bust_risk_score(buffett, cape, hy, vix, fg=50):
    """
    Risco de Bear Market — bust = EUFORIA + COMPLACÊNCIA simultâneas.
    Em fases de pânico (F&G baixo, VIX alto) o score fica baixo por definição.
    """
    h_euphoria = clamp(((fg - 50) / 50) * 100, 0, 100) if fg > 50 else 0
    h_vix = clamp(100 - ((vix - 10) / 15) * 100, 0, 100) if vix < 25 else 0
    h_credit = clamp(100 - ((hy - 1.5) / 2.5) * 100, 0, 100) if hy < 4.0 else 0
    h_valuation = 0
    if cape > 32 and buffett > 160:
        h_valuation = clamp(
            ((cape - 32) / 10) * 50 + ((buffett - 160) / 40) * 50, 0, 100)

    return round(
        h_euphoria  * 0.35 +
        h_vix       * 0.30 +
        h_credit    * 0.20 +
        h_valuation * 0.15
    )


def gather_market_data():
    """Busca os 6 indicadores em PARALELO e calcula os scores."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        f_fg   = ex.submit(get_fear_greed_index)
        f_vix  = ex.submit(get_vix)
        f_sp   = ex.submit(get_sp500_vs_ma200)
        f_buff = ex.submit(get_buffett_indicator)
        f_cape = ex.submit(get_shiller_pe)
        f_hy   = ex.submit(get_high_yield_spread)

    fg, vix, sp = f_fg.result(), f_vix.result(), f_sp.result()
    buff, cape, hy = f_buff.result(), f_cape.result(), f_hy.result()

    score = calculate_score(fg['value'], vix['value'], sp['value'],
                            buff['value'], cape['value'], hy['value'])
    bust = calculate_bust_risk_score(buff['value'], cape['value'],
                                     hy['value'], vix['value'], fg['value'])
    return {
        'score':     score,
        'bustScore': bust,
        'fearGreed': fg['value'],
        'vix':       vix['value'],
        'breadth':   sp['value'],
        'buffett':   buff['value'],
        'cape':      cape['value'],
        'hySpread':  hy['value'],
        'indicators': {
            'Fear & Greed Index':  fg,
            'VIX (Volatilidade)':  vix,
            'S&P vs MA200':        sp,
            'Buffett Indicator':   buff,
            'Shiller P/E (CAPE)':  cape,
            'High Yield Spread':   hy,
        },
        'timestamp':  now_utc().isoformat(),
        'cache_note': 'Dados renovados a cada 6 horas'
    }


# ─────────────────────────────────────────────────────────────────────────────
# ALERTAS POR EMAIL (estado vive na memória do web service — não em /tmp
# de um container de cron efémero, onde nunca persistia)
# ─────────────────────────────────────────────────────────────────────────────

SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", 587))
SMTP_USER = os.getenv("ALERT_SMTP_USER", "")
SMTP_PASS = os.getenv("ALERT_SMTP_PASS", "")
EMAIL_TO  = os.getenv("ALERT_EMAIL_TO", "")

BUY_THRESHOLD    = int(os.getenv("ALERT_BUY_THRESHOLD",    "30"))
SELL_THRESHOLD   = int(os.getenv("ALERT_SELL_THRESHOLD",   "70"))
HUNTER_THRESHOLD = int(os.getenv("ALERT_HUNTER_THRESHOLD", "75"))

ALERT_COOLDOWN = timedelta(hours=6)
_alert_state = {"last_buy": None, "last_sell": None, "last_bust": None}


def _cooldown_ok(key):
    last = _alert_state.get(key)
    return last is None or now_utc() - last >= ALERT_COOLDOWN


def send_alert_email(subject, score, bust, alert_type, data):
    color = ("#00c896" if alert_type == "buy" else
             "#ff4757" if alert_type == "sell" else "#ff7043")
    label = {"buy":  "OPORTUNIDADE DE COMPRA",
             "sell": "SINAL DE VENDA / PROTEGER",
             "bust": "RISCO DE BEAR MARKET ELEVADO"}[alert_type]
    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    rows = "".join(
        f"<tr><td style='padding:7px 0;color:#5c6480;font-size:13px;border-bottom:1px solid #1e2332;'>{k}</td>"
        f"<td style='padding:7px 0;text-align:right;font-family:monospace;font-size:14px;border-bottom:1px solid #1e2332;'>{v}</td></tr>"
        for k, v in {
            "Fear & Greed":  f"{data['fearGreed']:.0f}",
            "VIX":           f"{data['vix']:.1f}",
            "Buffett":       f"{data['buffett']:.0f}%",
            "CAPE":          f"{data['cape']:.1f}",
            "S&P vs MA200":  f"{data['breadth']:+.1f}%",
            "HY Spread":     f"{data['hySpread']:.2f}%",
        }.items()
    )

    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#0d0f14;font-family:'Segoe UI',sans-serif;">
<div style="max-width:480px;margin:30px auto;background:#141720;border-radius:16px;overflow:hidden;border:1px solid #1e2332;">
  <div style="background:{color}14;border-top:3px solid {color};padding:24px 24px 20px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:{color};margin-bottom:10px;">Indicador NB &nbsp;&middot;&nbsp; {now}</div>
    <div style="font-size:20px;font-weight:700;color:#dde1ec;margin-bottom:8px;">{label}</div>
    <div style="font-size:52px;font-weight:500;font-family:monospace;color:{color};line-height:1;">{score}<span style="font-size:.35em;color:#5c6480;margin-left:4px;">/100</span></div>
  </div>
  <div style="padding:20px 24px;">
    <div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#5c6480;margin-bottom:10px;">Indicadores</div>
    <table style="width:100%;border-collapse:collapse;">{rows}</table>
    <div style="margin-top:16px;background:#0d0f14;border-radius:8px;padding:14px;display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:12px;color:#5c6480;">Risco de Bear Market</div>
      <div style="font-family:monospace;font-size:22px;color:#ff7043;font-weight:500;">{bust}<span style="font-size:.45em;color:#5c6480;">/100</span></div>
    </div>
    <div style="margin-top:20px;text-align:center;">
      <a href="https://indicador-nb.onrender.com" style="background:#f0b429;color:#0d0f14;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px;">Abrir Indicador NB</a>
    </div>
    <div style="margin-top:20px;font-size:11px;color:#3d4460;text-align:center;">Ferramenta de análise pessoal. Não é aconselhamento financeiro.</div>
  </div>
</div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Indicador NB <{SMTP_USER}>"
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    print(f"[alerts] Email enviado: {subject}")


# ─────────────────────────────────────────────────────────────────────────────
# ROTAS
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_file('contrarian_web_app.html')


@app.route('/api/data')
def get_market_data():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] /api/data")
    return jsonify(gather_market_data())


@app.route('/api/check-alerts')
def check_alerts():
    """Chamado pelo cron a cada 30 min. Usa o MESMO score do site."""
    if not SMTP_USER or not SMTP_PASS or not EMAIL_TO:
        return jsonify({'status': 'smtp_not_configured', 'sent': []}), 200

    data = gather_market_data()
    score, bust = data['score'], data['bustScore']
    sent = []

    print(f"[alerts] Score={score} Bust={bust} "
          f"Buy<={BUY_THRESHOLD} Sell>={SELL_THRESHOLD} Bust>={HUNTER_THRESHOLD}")

    checks = [
        ('buy',  score <= BUY_THRESHOLD,
         f"[Indicador NB] Score {score}/100 — Oportunidade de Compra"),
        ('sell', score >= SELL_THRESHOLD,
         f"[Indicador NB] Score {score}/100 — Reduz Exposição"),
        ('bust', HUNTER_THRESHOLD > 0 and bust >= HUNTER_THRESHOLD,
         f"[Indicador NB] Risco de Bear Market {bust}/100 — Risco Elevado"),
    ]
    for alert_type, condition, subject in checks:
        key = f"last_{alert_type}"
        if condition and _cooldown_ok(key):
            try:
                send_alert_email(subject, score, bust, alert_type, data)
                _alert_state[key] = now_utc()
                sent.append(alert_type)
            except Exception as e:
                print(f"[alerts] Falha ao enviar '{alert_type}': {e}")

    return jsonify({'status': 'ok', 'score': score, 'bust': bust, 'sent': sent})


@app.route('/api/health')
def health():
    return jsonify({
        'status':      'ok',
        'timestamp':   now_utc().isoformat(),
        'cache_items': len(_cache),
        'alert_state': {k: (v.isoformat() if v else None)
                        for k, v in _alert_state.items()},
        'api_keys': {
            'fred': API_KEYS['fred'] != 'YOUR_FRED_KEY',
        },
        'smtp_configured': bool(SMTP_USER and SMTP_PASS and EMAIL_TO),
    })


@app.route('/ping')
def ping():
    return 'pong', 200


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

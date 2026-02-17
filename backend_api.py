#!/usr/bin/env python3
"""
Indicador NB - Backend API
100% automático, sem necessidade de actualizações manuais.
"""

from flask import Flask, jsonify, send_file
from flask_cors import CORS
import requests
import os
import re
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

API_KEYS = {
    'alpha_vantage': os.getenv('ALPHA_VANTAGE_KEY', 'DEMO'),
    'fred': os.getenv('FRED_KEY', 'YOUR_FRED_KEY'),
}

# Cache: guarda resultados por 6 horas para não gastar limites das APIs
_cache = {}
CACHE_DURATION = timedelta(hours=6)

def get_cached(key):
    if key in _cache:
        data, timestamp = _cache[key]
        if datetime.now() - timestamp < CACHE_DURATION:
            return data
    return None

def set_cached(key, data):
    _cache[key] = (data, datetime.now())

# ============================================================================
# INDICADORES - TODOS AUTOMÁTICOS
# ============================================================================

def get_fear_greed_index():
    cached = get_cached('fear_greed')
    if cached:
        return cached

    try:
        url = "https://api.alternative.me/fng/?limit=30"
        r = requests.get(url, timeout=10)
        data = r.json()

        current = int(data['data'][0]['value'])
        values = [int(d['value']) for d in data['data']]
        avg_30d = sum(values) / len(values)

        result = {
            'value': current,
            'avg_30d': round(avg_30d, 1),
            'signal': 'STRONG_BUY' if current < 20 else 'BUY' if current < 35 else
                      'SELL' if current > 75 else 'CAUTION' if current > 65 else 'NEUTRAL',
            'description': f'Actual: {current}, Média 30d: {avg_30d:.0f}'
        }
        set_cached('fear_greed', result)
        return result
    except Exception as e:
        print(f"Erro Fear & Greed: {e}")
        return {'value': 50, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_vix():
    cached = get_cached('vix')
    if cached:
        return cached

    try:
        url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=VIX&apikey={API_KEYS["alpha_vantage"]}'
        r = requests.get(url, timeout=10)
        data = r.json()

        if 'Global Quote' not in data or '05. price' not in data['Global Quote']:
            raise Exception("VIX não disponível")

        vix = float(data['Global Quote']['05. price'])
        result = {
            'value': round(vix, 1),
            'signal': 'STRONG_BUY' if vix > 35 else 'BUY' if vix > 25 else
                      'SELL' if vix < 12 else 'CAUTION' if vix < 15 else 'NEUTRAL',
            'description': f'VIX: {vix:.1f}'
        }
        set_cached('vix', result)
        return result
    except Exception as e:
        print(f"Erro VIX: {e}")
        return {'value': 18, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_sp500_vs_ma200():
    cached = get_cached('sp500_ma200')
    if cached:
        return cached

    try:
        url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey={API_KEYS["alpha_vantage"]}'
        r = requests.get(url, timeout=10)
        price = float(r.json()['Global Quote']['05. price'])

        url_sma = f'https://www.alphavantage.co/query?function=SMA&symbol=SPY&interval=daily&time_period=200&series_type=close&apikey={API_KEYS["alpha_vantage"]}'
        r_sma = requests.get(url_sma, timeout=10)
        sma_200 = float(list(r_sma.json()['Technical Analysis: SMA'].values())[0]['SMA'])

        pct = ((price / sma_200) - 1) * 100
        result = {
            'value': round(pct, 1),
            'signal': 'STRONG_BUY' if pct < -15 else 'BUY' if pct < -5 else
                      'SELL' if pct > 15 else 'CAUTION' if pct > 10 else 'NEUTRAL',
            'description': f'S&P vs MA200: {pct:+.1f}%'
        }
        set_cached('sp500_ma200', result)
        return result
    except Exception as e:
        print(f"Erro S&P/MA200: {e}")
        return {'value': 0, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_shiller_pe():
    """
    Scraping automático do multpl.com.
    Sem necessidade de actualização manual.
    """
    cached = get_cached('shiller_pe')
    if cached:
        return cached

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; indicadornb/1.0)'}
        r = requests.get('https://www.multpl.com/shiller-pe', headers=headers, timeout=10)

        # Procurar o valor actual na página
        match = re.search(r'<div id="current"[^>]*>\s*([\d.]+)', r.text)
        if not match:
            # Fallback: tentar outro padrão
            match = re.search(r'Shiller PE Ratio[^<]*<[^>]+>([\d.]+)', r.text)

        if match:
            cape = float(match.group(1))
        else:
            # Segunda fonte: stooq via FRED (serie CAPE do professor Shiller)
            url_fred = f'https://api.stlouisfed.org/fred/series/observations?series_id=CAPE&api_key={API_KEYS["fred"]}&file_type=json&limit=1&sort_order=desc'
            r2 = requests.get(url_fred, timeout=10)
            cape = float(r2.json()['observations'][0]['value'])

        result = {
            'value': round(cape, 1),
            'signal': 'SELL' if cape > 30 else 'CAUTION' if cape > 25 else
                      'BUY' if cape < 18 else 'STRONG_BUY' if cape < 15 else 'NEUTRAL',
            'description': f'Shiller P/E: {cape:.1f}'
        }
        set_cached('shiller_pe', result)
        print(f"Shiller P/E obtido automaticamente: {cape}")
        return result

    except Exception as e:
        print(f"Erro Shiller P/E scraping: {e}. A usar valor de fallback.")
        # Fallback conservador - indica para rever manualmente
        return {'value': 31.0, 'signal': 'SELL', 'description': 'Shiller P/E: ~31 (verificar multpl.com)'}


def get_buffett_indicator():
    """
    Buffett Indicator 100% automático.
    Market cap via Wilshire 5000 (FRED) a dividir pelo PIB (FRED).
    Ambos da mesma fonte, sem estimativas manuais.
    """
    cached = get_cached('buffett')
    if cached:
        return cached

    try:
        fred_key = API_KEYS['fred']

        # Wilshire 5000 Total Market Index (capitalização total do mercado americano)
        url_wilshire = f'https://api.stlouisfed.org/fred/series/observations?series_id=WILL5000PR&api_key={fred_key}&file_type=json&limit=1&sort_order=desc'
        r_w = requests.get(url_wilshire, timeout=10)
        wilshire = float(r_w.json()['observations'][0]['value'])

        # PIB dos EUA (em biliões)
        url_gdp = f'https://api.stlouisfed.org/fred/series/observations?series_id=GDP&api_key={fred_key}&file_type=json&limit=1&sort_order=desc'
        r_g = requests.get(url_gdp, timeout=10)
        gdp = float(r_g.json()['observations'][0]['value'])  # já em biliões

        # Wilshire está em biliões USD, GDP está em biliões USD
        ratio = (wilshire / gdp) * 100

        result = {
            'value': round(ratio, 0),
            'signal': 'SELL' if ratio > 140 else 'CAUTION' if ratio > 120 else
                      'BUY' if ratio < 80 else 'STRONG_BUY' if ratio < 60 else 'NEUTRAL',
            'description': f'Market/GDP: {ratio:.0f}%'
        }
        set_cached('buffett', result)
        print(f"Buffett Indicator obtido automaticamente: {ratio:.0f}%")
        return result

    except Exception as e:
        print(f"Erro Buffett Indicator: {e}")
        return {'value': 100, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_high_yield_spread():
    cached = get_cached('hy_spread')
    if cached:
        return cached

    try:
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2&api_key={API_KEYS["fred"]}&file_type=json&limit=1&sort_order=desc'
        r = requests.get(url, timeout=10)
        spread = float(r.json()['observations'][0]['value'])

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
        return {'value': 4, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


# ============================================================================
# CALCULAR SCORE FINAL
# ============================================================================

def calculate_score(fear_greed, vix, breadth, buffett, cape, hy_spread):
    scores = {
        'fear_greed': fear_greed,
        'vix': 100 - min(100, max(0, ((vix - 10) / 30) * 100)),
        'buffett': min(100, max(0, ((buffett - 60) / 80) * 100)),
        'cape': min(100, max(0, ((cape - 10) / 25) * 100)),
        'breadth': min(100, max(0, 50 + breadth * 2)),
        'hy_spread': 100 - min(100, max(0, (hy_spread / 10) * 100)),
    }
    return round(
        scores['fear_greed'] * 0.30 +
        scores['vix'] * 0.20 +
        scores['buffett'] * 0.15 +
        scores['cape'] * 0.15 +
        scores['breadth'] * 0.10 +
        scores['hy_spread'] * 0.10
    )


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.route('/')
def index():
    return send_file('contrarian_web_app.html')


@app.route('/api/data')
def get_market_data():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Request /api/data")

    fg    = get_fear_greed_index()
    vix   = get_vix()
    sp    = get_sp500_vs_ma200()
    buff  = get_buffett_indicator()
    cape  = get_shiller_pe()
    hy    = get_high_yield_spread()

    score = calculate_score(
        fg['value'], vix['value'], sp['value'],
        buff['value'], cape['value'], hy['value']
    )

    return jsonify({
        'score': score,
        'fearGreed': fg['value'],
        'vix': vix['value'],
        'breadth': sp['value'],
        'buffett': buff['value'],
        'cape': cape['value'],
        'hySpread': hy['value'],
        'indicators': {
            'Fear & Greed Index': fg,
            'VIX (Volatilidade)': vix,
            'S&P vs MA200': sp,
            'Buffett Indicator': buff,
            'Shiller P/E (CAPE)': cape,
            'High Yield Spread': hy,
        },
        'timestamp': datetime.now().isoformat(),
        'cache_note': 'Dados renovados a cada 6 horas'
    })


@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'cache_items': len(_cache),
        'api_keys': {
            'alpha_vantage': API_KEYS['alpha_vantage'] != 'DEMO',
            'fred': API_KEYS['fred'] != 'YOUR_FRED_KEY',
        }
    })


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

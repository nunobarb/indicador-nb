#!/usr/bin/env python3
"""
Indicador NB - Backend API
100% automático, sem actualizações manuais.
"""

from flask import Flask, jsonify, send_file
from flask_cors import CORS
import requests
import os
import re
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

API_KEYS = {
    'alpha_vantage': os.getenv('ALPHA_VANTAGE_KEY', 'DEMO'),
    'fred': os.getenv('FRED_KEY', 'YOUR_FRED_KEY'),
}

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


def get_fear_greed_index():
    cached = get_cached('fear_greed')
    if cached: return cached
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=30", timeout=10)
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
    """VIX via FRED - mais fiável que Alpha Vantage para este símbolo"""
    cached = get_cached('vix')
    if cached: return cached
    try:
        # VIXCLS = VIX closing price, série oficial do FRED
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&api_key={API_KEYS["fred"]}&file_type=json&limit=1&sort_order=desc'
        r = requests.get(url, timeout=10)
        data = r.json()
        # Filtrar valores inválidos (FRED usa '.' para dias sem dados)
        obs = [o for o in data['observations'] if o['value'] != '.']
        vix = float(obs[0]['value'])
        result = {
            'value': round(vix, 1),
            'signal': 'STRONG_BUY' if vix > 35 else 'BUY' if vix > 25 else
                      'SELL' if vix < 12 else 'CAUTION' if vix < 15 else 'NEUTRAL',
            'description': f'VIX: {vix:.1f}'
        }
        set_cached('vix', result)
        print(f"VIX: {vix}")
        return result
    except Exception as e:
        print(f"Erro VIX: {e}")
        return {'value': 18, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_sp500_vs_ma200():
    """S&P 500 via FRED (SP500 series) e calcula desvio manualmente"""
    cached = get_cached('sp500_ma200')
    if cached: return cached
    try:
        fred_key = API_KEYS['fred']
        # Buscar últimos 300 dias do S&P 500 para calcular MA200
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=SP500&api_key={fred_key}&file_type=json&limit=300&sort_order=desc'
        r = requests.get(url, timeout=10)
        data = r.json()
        # Filtrar valores válidos
        obs = [float(o['value']) for o in data['observations'] if o['value'] != '.']
        if len(obs) < 200:
            raise Exception("Dados insuficientes para MA200")
        price = obs[0]           # Preço mais recente
        ma200 = sum(obs[:200]) / 200  # Média dos últimos 200 dias
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
        return {'value': 0, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_shiller_pe():
    cached = get_cached('shiller_pe')
    if cached: return cached
    try:
        # Fonte 1: scraping do multpl.com
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; indicadornb/1.0)'}
        r = requests.get('https://www.multpl.com/shiller-pe', headers=headers, timeout=10)
        match = re.search(r'<div id="current"[^>]*>\s*([\d.]+)', r.text)
        if not match:
            match = re.search(r'Shiller PE Ratio[^<]*<[^>]+>([\d.]+)', r.text)
        if match:
            cape = float(match.group(1))
        else:
            # Fonte 2: FRED série CAPE
            url_fred = f'https://api.stlouisfed.org/fred/series/observations?series_id=CAPE&api_key={API_KEYS["fred"]}&file_type=json&limit=5&sort_order=desc'
            r2 = requests.get(url_fred, timeout=10)
            obs = [o for o in r2.json()['observations'] if o['value'] != '.']
            cape = float(obs[0]['value'])
        result = {
            'value': round(cape, 1),
            'signal': 'SELL' if cape > 30 else 'CAUTION' if cape > 25 else
                      'BUY' if cape < 18 else 'STRONG_BUY' if cape < 15 else 'NEUTRAL',
            'description': f'Shiller P/E: {cape:.1f}'
        }
        set_cached('shiller_pe', result)
        print(f"Shiller P/E: {cape}")
        return result
    except Exception as e:
        print(f"Erro Shiller P/E: {e}")
        return {'value': 31.0, 'signal': 'SELL', 'description': 'Shiller P/E: ~31'}


def get_buffett_indicator():
    """Buffett Indicator via FRED - usa DDDM (Wilshire 5000 market cap em USD)"""
    cached = get_cached('buffett')
    if cached: return cached
    try:
        fred_key = API_KEYS['fred']
        # DDDM = Wilshire 5000 Full Cap Price Index em biliões USD
        url_w = f'https://api.stlouisfed.org/fred/series/observations?series_id=DDDM01USA156NWDB&api_key={fred_key}&file_type=json&limit=5&sort_order=desc'
        r_w = requests.get(url_w, timeout=10)
        obs_w = [o for o in r_w.json()['observations'] if o['value'] != '.']
        market_cap = float(obs_w[0]['value'])  # em biliões USD

        # GDP nominal em biliões USD
        url_gdp = f'https://api.stlouisfed.org/fred/series/observations?series_id=GDP&api_key={fred_key}&file_type=json&limit=5&sort_order=desc'
        r_g = requests.get(url_gdp, timeout=10)
        obs_g = [o for o in r_g.json()['observations'] if o['value'] != '.']
        gdp = float(obs_g[0]['value'])

        ratio = (market_cap / gdp) * 100
        result = {
            'value': round(ratio, 0),
            'signal': 'SELL' if ratio > 140 else 'CAUTION' if ratio > 120 else
                      'BUY' if ratio < 80 else 'STRONG_BUY' if ratio < 60 else 'NEUTRAL',
            'description': f'Market/GDP: {ratio:.0f}%'
        }
        set_cached('buffett', result)
        print(f"Buffett Indicator: {ratio:.0f}%")
        return result
    except Exception as e:
        print(f"Erro Buffett: {e}")
        return {'value': 100, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_high_yield_spread():
    cached = get_cached('hy_spread')
    if cached: return cached
    try:
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2&api_key={API_KEYS["fred"]}&file_type=json&limit=5&sort_order=desc'
        r = requests.get(url, timeout=10)
        obs = [o for o in r.json()['observations'] if o['value'] != '.']
        spread = float(obs[0]['value'])
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


def calculate_score(fg, vix, breadth, buffett, cape, hy):
    scores = {
        'fear_greed': fg,
        'vix':        100 - min(100, max(0, ((vix - 10) / 30) * 100)),
        'buffett':    min(100, max(0, ((buffett - 60) / 80) * 100)),
        'cape':       min(100, max(0, ((cape - 10) / 25) * 100)),
        'breadth':    min(100, max(0, 50 + breadth * 2)),
        'hy_spread':  100 - min(100, max(0, (hy / 10) * 100)),
    }
    return round(
        scores['fear_greed'] * 0.30 +
        scores['vix']        * 0.20 +
        scores['buffett']    * 0.15 +
        scores['cape']       * 0.15 +
        scores['breadth']    * 0.10 +
        scores['hy_spread']  * 0.10
    )


@app.route('/')
def index():
    return send_file('contrarian_web_app.html')


@app.route('/api/data')
def get_market_data():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] /api/data")
    fg   = get_fear_greed_index()
    vix  = get_vix()
    sp   = get_sp500_vs_ma200()
    buff = get_buffett_indicator()
    cape = get_shiller_pe()
    hy   = get_high_yield_spread()
    score = calculate_score(
        fg['value'], vix['value'], sp['value'],
        buff['value'], cape['value'], hy['value']
    )
    return jsonify({
        'score':    score,
        'fearGreed': fg['value'],
        'vix':      vix['value'],
        'breadth':  sp['value'],
        'buffett':  buff['value'],
        'cape':     cape['value'],
        'hySpread': hy['value'],
        'indicators': {
            'Fear & Greed Index':  fg,
            'VIX (Volatilidade)':  vix,
            'S&P vs MA200':        sp,
            'Buffett Indicator':   buff,
            'Shiller P/E (CAPE)':  cape,
            'High Yield Spread':   hy,
        },
        'timestamp':  datetime.now().isoformat(),
        'cache_note': 'Dados renovados a cada 6 horas'
    })


@app.route('/api/health')
def health():
    return jsonify({
        'status':    'ok',
        'timestamp': datetime.now().isoformat(),
        'cache_items': len(_cache),
        'api_keys': {
            'alpha_vantage': API_KEYS['alpha_vantage'] != 'DEMO',
            'fred':          API_KEYS['fred'] != 'YOUR_FRED_KEY',
        }
    })


@app.route('/ping')
def ping():
    return 'pong', 200


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

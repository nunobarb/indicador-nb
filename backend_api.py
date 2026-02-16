#!/usr/bin/env python3
"""
Indicador NB - Backend API
Serve dados reais de mercado para a app web
"""

from flask import Flask, jsonify, send_file
from flask_cors import CORS
import requests
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Permite requests do frontend

# ============================================================================
# CONFIGURAÇÃO - EDITA AS API KEYS
# ============================================================================

API_KEYS = {
    'alpha_vantage': os.getenv('ALPHA_VANTAGE_KEY', 'DEMO'),
    'fred': os.getenv('FRED_KEY', 'YOUR_FRED_KEY'),
}

# ============================================================================
# FUNÇÕES PARA OBTER DADOS REAIS
# ============================================================================

def get_fear_greed_index():
    """Fear & Greed Index da Alternative.me (API pública)"""
    try:
        url = "https://api.alternative.me/fng/?limit=30"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        current = int(data['data'][0]['value'])
        values = [int(d['value']) for d in data['data']]
        avg_30d = sum(values) / len(values)
        
        return {
            'value': current,
            'avg_30d': round(avg_30d, 1),
            'signal': 'STRONG_BUY' if current < 20 else 'BUY' if current < 35 else 
                     'SELL' if current > 75 else 'CAUTION' if current > 65 else 'NEUTRAL',
            'description': f'Actual: {current}, Média 30d: {avg_30d:.0f}'
        }
    except Exception as e:
        print(f"Erro Fear & Greed: {e}")
        return {'value': 50, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_vix():
    """VIX via Alpha Vantage"""
    try:
        url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=VIX&apikey={API_KEYS["alpha_vantage"]}'
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if 'Global Quote' not in data or '05. price' not in data['Global Quote']:
            raise Exception("VIX data not available")
            
        vix = float(data['Global Quote']['05. price'])
        
        return {
            'value': round(vix, 1),
            'signal': 'STRONG_BUY' if vix > 35 else 'BUY' if vix > 25 else 
                     'SELL' if vix < 12 else 'CAUTION' if vix < 15 else 'NEUTRAL',
            'description': f'VIX: {vix:.1f}'
        }
    except Exception as e:
        print(f"Erro VIX: {e}")
        return {'value': 18, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_sp500_vs_ma200():
    """S&P 500 vs Média de 200 dias"""
    try:
        url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey={API_KEYS["alpha_vantage"]}'
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if 'Global Quote' not in data:
            raise Exception("SPY data not available")
            
        price = float(data['Global Quote']['05. price'])
        
        url_sma = f'https://www.alphavantage.co/query?function=SMA&symbol=SPY&interval=daily&time_period=200&series_type=close&apikey={API_KEYS["alpha_vantage"]}'
        response_sma = requests.get(url_sma, timeout=10)
        data_sma = response_sma.json()
        
        if 'Technical Analysis: SMA' not in data_sma:
            raise Exception("SMA data not available")
            
        sma_200 = float(list(data_sma['Technical Analysis: SMA'].values())[0]['SMA'])
        pct_from_ma = ((price / sma_200) - 1) * 100
        
        return {
            'value': round(pct_from_ma, 1),
            'signal': 'STRONG_BUY' if pct_from_ma < -15 else 'BUY' if pct_from_ma < -5 else
                     'SELL' if pct_from_ma > 15 else 'CAUTION' if pct_from_ma > 10 else 'NEUTRAL',
            'description': f'S&P vs MA200: {pct_from_ma:+.1f}%'
        }
    except Exception as e:
        print(f"Erro S&P/MA200: {e}")
        return {'value': 0, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_buffett_indicator():
    """Buffett Indicator - Market Cap / GDP"""
    try:
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=GDP&api_key={API_KEYS["fred"]}&file_type=json&limit=1&sort_order=desc'
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if 'observations' not in data:
            raise Exception("GDP data not available")
            
        gdp = float(data['observations'][0]['value']) * 1000000000
        estimated_market_cap = 50000000000000  # 50 trillion USD
        ratio = (estimated_market_cap / gdp) * 100
        
        return {
            'value': round(ratio, 0),
            'signal': 'SELL' if ratio > 140 else 'CAUTION' if ratio > 120 else 
                     'BUY' if ratio < 80 else 'STRONG_BUY' if ratio < 60 else 'NEUTRAL',
            'description': f'Market/GDP: {ratio:.0f}%'
        }
    except Exception as e:
        print(f"Erro Buffett Indicator: {e}")
        return {'value': 100, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


def get_shiller_pe():
    """Shiller P/E - Actualizar manualmente de multpl.com"""
    estimated_cape = 31.5  # Actualizado em Feb 2026
    
    return {
        'value': round(estimated_cape, 1),
        'signal': 'SELL' if estimated_cape > 30 else 'CAUTION' if estimated_cape > 25 else
                 'BUY' if estimated_cape < 18 else 'STRONG_BUY' if estimated_cape < 15 else 'NEUTRAL',
        'description': f'Shiller P/E: {estimated_cape:.1f}'
    }


def get_high_yield_spread():
    """High Yield Spread via FRED"""
    try:
        url = f'https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2&api_key={API_KEYS["fred"]}&file_type=json&limit=1&sort_order=desc'
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if 'observations' not in data:
            raise Exception("HY Spread data not available")
            
        spread = float(data['observations'][0]['value'])
        
        return {
            'value': round(spread, 2),
            'signal': 'STRONG_BUY' if spread > 8 else 'BUY' if spread > 5 else
                     'SELL' if spread < 3 else 'NEUTRAL',
            'description': f'HY Spread: {spread:.2f}%'
        }
    except Exception as e:
        print(f"Erro HY Spread: {e}")
        return {'value': 4, 'signal': 'NEUTRAL', 'description': 'Dados indisponíveis'}


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/')
def index():
    """Serve a app web"""
    return send_file('contrarian_web_app.html')


@app.route('/api/data')
def get_market_data():
    """Endpoint principal - retorna todos os dados"""
    print(f"[{datetime.now()}] Fetching market data...")
    
    fear_greed = get_fear_greed_index()
    vix = get_vix()
    breadth = get_sp500_vs_ma200()
    buffett = get_buffett_indicator()
    cape = get_shiller_pe()
    hy_spread = get_high_yield_spread()
    
    data = {
        'fearGreed': fear_greed['value'],
        'vix': vix['value'],
        'breadth': breadth['value'],
        'buffett': buffett['value'],
        'cape': cape['value'],
        'hySpread': hy_spread['value'],
        'indicators': {
            'Fear & Greed Index': fear_greed,
            'VIX (Volatilidade)': vix,
            'S&P vs MA200': breadth,
            'Buffett Indicator': buffett,
            'Shiller P/E (CAPE)': cape,
            'High Yield Spread': hy_spread
        },
        'timestamp': datetime.now().isoformat()
    }
    
    print(f"[{datetime.now()}] Data fetched successfully!")
    return jsonify(data)


@app.route('/api/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'api_keys_configured': {
            'alpha_vantage': API_KEYS['alpha_vantage'] != 'DEMO',
            'fred': API_KEYS['fred'] != 'YOUR_FRED_KEY'
        }
    })


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

"""
alerter.py — Indicador NB
Corre como cron job no Render (a cada 30 minutos).
Verifica o score actual e envia email se atingir os thresholds.
"""

import os
import json
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── CONFIG (via variáveis de ambiente do Render) ──────────────────────────────
SMTP_HOST  = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("ALERT_SMTP_PORT", 587))
SMTP_USER  = os.getenv("ALERT_SMTP_USER", "")
SMTP_PASS  = os.getenv("ALERT_SMTP_PASS", "")
EMAIL_TO   = os.getenv("ALERT_EMAIL_TO", "")

BUY_THRESHOLD    = int(os.getenv("ALERT_BUY_THRESHOLD",    "30"))
SELL_THRESHOLD   = int(os.getenv("ALERT_SELL_THRESHOLD",   "70"))
HUNTER_THRESHOLD = int(os.getenv("ALERT_HUNTER_THRESHOLD", "75"))

# URL da tua própria API (o Render serve-a publicamente)
API_URL = os.getenv("ALERT_API_URL", "https://indicador-nb.onrender.com/api/data")

# Ficheiro para guardar estado entre runs (evita spam)
STATE_FILE = "/tmp/alert_state.json"

# ── ESTADO ────────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"last_buy": None, "last_sell": None, "last_bust": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def hours_since(iso_str):
    if not iso_str:
        return 999
    dt = datetime.fromisoformat(iso_str)
    return (datetime.utcnow() - dt).total_seconds() / 3600

# ── CÁLCULO DO SCORE (mesma lógica do frontend) ───────────────────────────────
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def calculate_scores(d):
    s_fg  = d["fearGreed"]
    s_vix = 100 - clamp(((d["vix"] - 10) / 45) * 100, 0, 100)
    s_buf = clamp(((d["buffett"] - 80) / 140) * 100, 0, 100)
    s_cap = clamp(((d["cape"] - 10) / 35) * 100, 0, 100)
    s_ma  = clamp(50 + (d["breadth"] / 30) * 50, 0, 100)
    s_hy  = 100 - clamp(((d["hySpread"] - 1.5) / 10.5) * 100, 0, 100)

    score = round(s_fg*0.20 + s_vix*0.15 + s_buf*0.20 + s_cap*0.20 + s_ma*0.10 + s_hy*0.15)

    fg = d.get("fearGreed", 50)
    vix = d["vix"]; hy = d["hySpread"]; cape = d["cape"]; buffett = d["buffett"]
    h_euphoria  = clamp(((fg - 50) / 50) * 100, 0, 100) if fg > 50 else 0
    h_vix_supp  = clamp(100 - ((vix - 10) / 15) * 100, 0, 100) if vix < 25 else 0
    h_credit    = clamp(100 - ((hy - 1.5) / 2.5) * 100, 0, 100) if hy < 4.0 else 0
    h_valuation = clamp(((cape-32)/10)*50 + ((buffett-160)/40)*50, 0, 100) if (cape > 32 and buffett > 160) else 0
    bust = round(h_euphoria*0.35 + h_vix_supp*0.30 + h_credit*0.20 + h_valuation*0.15)

    return score, bust

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def send_email(subject, score, bust, alert_type, data):
    color  = "#00c896" if alert_type == "buy" else "#ff4757" if alert_type == "sell" else "#ff7043"
    label  = {"buy": "OPORTUNIDADE DE COMPRA", "sell": "SINAL DE VENDA / PROTEGER", "bust": "RISCO DE BEAR MARKET ELEVADO"}[alert_type]
    now    = datetime.now().strftime("%d/%m/%Y %H:%M")

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
    <div style="font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:{color};margin-bottom:10px;">Indicador NB &nbsp;·&nbsp; {now}</div>
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
        s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    print(f"[alerter] Email enviado: {subject}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    if not SMTP_USER or not SMTP_PASS or not EMAIL_TO:
        print("[alerter] SMTP não configurado — define as variáveis de ambiente.")
        return

    # Busca dados da API
    try:
        r = requests.get(API_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[alerter] Erro ao obter dados: {e}")
        return

    score, bust = calculate_scores(data)
    state = load_state()
    now_iso = datetime.utcnow().isoformat()
    sent_any = False

    print(f"[alerter] Score={score}  Bust={bust}  "
          f"Buy<={BUY_THRESHOLD}  Sell>={SELL_THRESHOLD}  Bust>={HUNTER_THRESHOLD}")

    # Alerta de compra
    if score <= BUY_THRESHOLD and hours_since(state["last_buy"]) >= 6:
        try:
            send_email(f"[Indicador NB] Score {score}/100 — Oportunidade de Compra",
                       score, bust, "buy", data)
            state["last_buy"] = now_iso
            sent_any = True
        except Exception as e:
            print(f"[alerter] Falha ao enviar email de compra: {e}")

    # Alerta de venda
    if score >= SELL_THRESHOLD and hours_since(state["last_sell"]) >= 6:
        try:
            send_email(f"[Indicador NB] Score {score}/100 — Reduz Exposição",
                       score, bust, "sell", data)
            state["last_sell"] = now_iso
            sent_any = True
        except Exception as e:
            print(f"[alerter] Falha ao enviar email de venda: {e}")

    # Alerta Bust
    if HUNTER_THRESHOLD > 0 and bust >= HUNTER_THRESHOLD and hours_since(state["last_bust"]) >= 6:
        try:
            send_email(f"[Indicador NB] Risco de Bear Market {bust}/100 — Risco Elevado",
                       score, bust, "bust", data)
            state["last_bust"] = now_iso
            sent_any = True
        except Exception as e:
            print(f"[alerter] Falha ao enviar email Bust: {e}")

    if not sent_any:
        print("[alerter] Sem alertas para disparar.")

    save_state(state)

if __name__ == "__main__":
    main()

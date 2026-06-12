"""
alerter.py — Indicador NB (v2)
Corre como cron job no Render (a cada 30 minutos).

Agora é apenas um TRIGGER: chama /api/check-alerts no web service, que:
  • calcula o score com a MESMA lógica do site (single source of truth —
    a versão anterior duplicava a fórmula e tinha pesos dessincronizados);
  • guarda o estado anti-spam na memória do processo de longa duração
    (a versão anterior usava /tmp num container de cron efémero, pelo que
    o cooldown de 6h nunca persistia e enviava email a cada 30 min);
  • envia os emails directamente.

Efeito secundário útil: este ping a cada 30 min mantém o web service
acordado no free tier do Render, eliminando os cold starts.
"""

import os
import sys
import requests

API_BASE = os.getenv("ALERT_API_URL", "https://indicador-nb.onrender.com/api/data")
# Derivar o endpoint de alertas a partir da var existente (compatibilidade)
CHECK_URL = API_BASE.replace("/api/data", "/api/check-alerts")


def main():
    try:
        # Timeout generoso: se o serviço estiver a fazer cold start,
        # dar-lhe tempo para arrancar e buscar os dados.
        r = requests.get(CHECK_URL, timeout=90)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[alerter] Erro ao contactar {CHECK_URL}: {e}")
        sys.exit(1)

    status = data.get("status")
    if status == "smtp_not_configured":
        print("[alerter] SMTP não configurado no web service — define as "
              "variáveis ALERT_SMTP_* e ALERT_EMAIL_TO no serviço 'indicador-nb'.")
        return

    sent = data.get("sent", [])
    print(f"[alerter] Score={data.get('score')} Bust={data.get('bust')} "
          f"Alertas enviados: {sent if sent else 'nenhum'}")


if __name__ == "__main__":
    main()

"""
comissoes_api.py
─────────────────
Envia o mesmo JSON publicado no GitHub para o banco interno, via endpoint
REST da Aços Vital.

Configuração (via .env ou variáveis de ambiente):
  COMISSOES_API_URL  → endpoint de destino (ex: https://api-test.acosvital.com.br/comissoes_provisoria)
  COMISSOES_API_KEY  → chave enviada no header x-api-key
"""

import logging
import os

import requests

log = logging.getLogger(__name__)


def _url() -> str:
    url = os.getenv("COMISSOES_API_URL", "")
    if not url:
        raise EnvironmentError(
            "COMISSOES_API_URL não definido. "
            "Adicione ao .env: COMISSOES_API_URL=https://api-test.acosvital.com.br/comissoes_provisoria"
        )
    return url


def _headers() -> dict[str, str]:
    api_key = os.getenv("COMISSOES_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "COMISSOES_API_KEY não definido. "
            "Adicione ao .env: COMISSOES_API_KEY=sua_chave"
        )
    return {
        "Accept":       "application/json",
        "Content-Type": "application/json",
        "x-api-key":    api_key,
    }


def publicar(payload: dict) -> bool:
    """
    Envia o payload via PUT para o endpoint de comissões provisórias.
    Retorna True em caso de sucesso, False em caso de qualquer falha
    (sem interromper a execução principal).
    """
    try:
        resp = requests.put(_url(), headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        log.info("  ✅ API comissões: payload enviado com sucesso.")
        return True

    except EnvironmentError as exc:
        log.error("  ❌ API comissões: configuração ausente — %s", exc)
        return False
    except requests.HTTPError as exc:
        log.error(
            "  ❌ API comissões: erro HTTP %s — %s",
            exc.response.status_code, exc.response.text[:300],
        )
        return False
    except Exception as exc:
        log.error("  ❌ API comissões: falha inesperada — %s", exc)
        return False

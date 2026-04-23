"""
github_publisher.py
───────────────────
Publica o JSON do dashboard em um repositório GitHub privado via API REST.
Não depende de `git` instalado na máquina — usa apenas requests.

Configuração (via .env ou variáveis de ambiente):
  GITHUB_TOKEN      → Personal Access Token com escopo "Contents: Read & Write"
  GITHUB_REPO       → ex: "minha-org/meu-repo-privado"
  GITHUB_BRANCH     → branch de destino (padrão: "main")
  GITHUB_JSON_PATH  → caminho do arquivo no repo (padrão: "data/{ANO_MES}.json")
                      Use {ANO_MES} como placeholder — será substituído pelo mês atual.

Como criar o PAT:
  1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
  2. "Generate new token"
  3. Resource owner: sua org ou conta
  4. Repository access: selecione apenas o repo do dashboard
  5. Permissions → Repository permissions → Contents → "Read and write"
  6. Copie o token gerado e adicione ao .env:
       GITHUB_TOKEN=github_pat_XXXXXXXXXX...
"""

import base64
import json
import logging
import os
from datetime import date

import requests

import config

log = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"


def _eh_mes_atual() -> bool:
    """Retorna True se config._MES corresponde ao mês corrente (ano + mês)."""
    hoje = date.today()
    return config._MES.year == hoje.year and config._MES.month == hoje.month


def _headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError(
            "GITHUB_TOKEN não definido. "
            "Adicione ao .env: GITHUB_TOKEN=github_pat_XXXXXXXXXX"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    repo = os.getenv("GITHUB_REPO", "")
    if not repo:
        raise EnvironmentError(
            "GITHUB_REPO não definido. "
            "Adicione ao .env: GITHUB_REPO=usuario/nome-do-repo"
        )
    return repo


def _branch() -> str:
    return os.getenv("GITHUB_BRANCH", "main")


def _obter_sha_atual(repo: str, caminho: str, branch: str, headers: dict) -> str | None:
    """
    Obtém o SHA do arquivo atual no repositório (necessário para atualizar).
    Retorna None se o arquivo ainda não existe (primeira publicação).
    """
    url  = f"{_API_BASE}/repos/{repo}/contents/{caminho}"
    resp = requests.get(url, headers=headers, params={"ref": branch}, timeout=15)

    if resp.status_code == 200:
        sha = resp.json().get("sha")
        log.debug("  GitHub: arquivo existente SHA=%s", sha)
        return sha
    if resp.status_code == 404:
        log.debug("  GitHub: arquivo não existe ainda — será criado.")
        return None

    resp.raise_for_status()


def _commit_arquivo(
    repo: str,
    branch: str,
    caminho: str,
    conteudo_b64: str,
    headers: dict,
    mensagem: str,
) -> None:
    """
    Cria ou substitui um único arquivo no repositório via PUT.
    Obtém o SHA atual automaticamente se necessário.
    Lança HTTPError em caso de falha.
    """
    sha_atual = _obter_sha_atual(repo, caminho, branch, headers)

    body: dict = {
        "message": mensagem,
        "content": conteudo_b64,
        "branch":  branch,
    }
    if sha_atual:
        body["sha"] = sha_atual

    url  = f"{_API_BASE}/repos/{repo}/contents/{caminho}"
    resp = requests.put(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()

    acao = "atualizado" if sha_atual else "criado"
    log.info("  ✅ GitHub: '%s' %s.", caminho, acao)


def publicar(payload: dict) -> bool:
    """
    Faz o commit do JSON no repositório GitHub seguindo a estrutura:

      history/{ANO_MES}.json   → sempre atualizado (mês atual ou passado)
      current_month.json       → atualizado apenas quando for o mês corrente

    Retorna True se todos os commits necessários foram bem-sucedidos,
    False em caso de qualquer falha (sem interromper a execução principal).
    """
    try:
        repo    = _repo()
        branch  = _branch()
        headers = _headers()

        conteudo_json = json.dumps(payload, ensure_ascii=False, indent=2)
        conteudo_b64  = base64.b64encode(conteudo_json.encode("utf-8")).decode("ascii")
        msg_base      = f"chore: atualiza dados {config.MES_REF} [{config.ANO_MES_REF}]"

        # ── 1. history/{ANO_MES}.json — sempre ────────────────────────────────
        caminho_history = f"history/{config.ANO_MES_REF}.json"
        _commit_arquivo(repo, branch, caminho_history, conteudo_b64, headers, msg_base)

        # ── 2. current_month.json — somente se for o mês atual ────────────────
        if _eh_mes_atual():
            _commit_arquivo(
                repo, branch,
                "current_month.json",
                conteudo_b64,
                headers,
                msg_base,
            )
            log.info("  ✅ GitHub: 'current_month.json' sincronizado.")
        else:
            log.info(
                "  ℹ️  GitHub: mês %s é passado — 'current_month.json' não alterado.",
                config.ANO_MES_REF,
            )

        return True

    except EnvironmentError as exc:
        log.error("  ❌ GitHub: configuração ausente — %s", exc)
        return False
    except requests.HTTPError as exc:
        log.error(
            "  ❌ GitHub: erro HTTP %s — %s",
            exc.response.status_code, exc.response.text[:300],
        )
        return False
    except Exception as exc:
        log.error("  ❌ GitHub: falha inesperada — %s", exc)
        return False

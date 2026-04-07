"""
clients.py
──────────
Toda comunicação com sistemas externos: TOTVS, OMIE e OneDrive.
Nenhuma regra de negócio aqui — só I/O.

Otimizações aplicadas:
  - OmieClient.nome_vendedor()     → cache bulk via ListarVendedores (1 chamada no __init__)
  - OmieClient.consultar_cliente() → cache lazy via ListarEmpresas (1 chamada por cliente novo)
  - Ambos thread-safe via threading.Lock para uso com ThreadPoolExecutor
"""

import logging
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

log = logging.getLogger(__name__)


def _session_com_retry(
    total: int = 5,
    backoff_factor: float = 2.0,
    status_forcelist: tuple = (425, 429, 500, 502, 503, 504),
) -> requests.Session:
    """
    Session com retry automático e backoff exponencial.
    Tentativas: 1ª imediata → 2s → 4s → 8s → 16s
    425 (rate limit OMIE) e 429 incluídos para retry automático.
    """
    session = requests.Session()
    retry = Retry(
        total            = total,
        backoff_factor   = backoff_factor,
        status_forcelist = status_forcelist,
        allowed_methods  = {"GET", "POST"},
        raise_on_status  = False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


# ═══════════════════════════════════════════════════════
#  OMIE
# ═══════════════════════════════════════════════════════

class OmieClient:
    """
    Encapsula todas as chamadas HTTP à API OMIE.

    Estratégia de cache:
      - Vendedores : pré-carregados em bulk no __init__ (ListarVendedores, ~70 itens, 1 chamada)
      - Clientes   : cache lazy por demanda (ListarEmpresas filtrando por código)
                     Thread-safe via Lock para uso com ThreadPoolExecutor.
    """

    _URL_PEDIDOS    = "https://app.omie.com.br/api/v1/produtos/pedido/"
    _URL_NF         = "https://app.omie.com.br/api/v1/produtos/nfconsultar/"
    _URL_CLIENTES   = "https://app.omie.com.br/api/v1/geral/clientes/"
    _URL_VENDEDORES = "https://app.omie.com.br/api/v1/geral/vendedores/"

    def __init__(self):
        self.app_key    = config.OMIE_APP_KEY
        self.app_secret = config.OMIE_APP_SECRET
        self._session   = _session_com_retry()

        # Cache bulk de vendedores {codigo: nome} — TTL 24h
        self._cache_vendedores: dict[int, str] = {}
        self._carregar_vendedores()

        # Cache bulk de empresas {codigo: nome} — TTL 7 dias
        # Sem chamada HTTP individual por cliente — lookup direto em memória
        self._cache_empresas: dict[int, str] = {}
        self._carregar_empresas()

        # Lock mantido apenas para thread-safety do cache em memória (sem HTTP)
        self._lock_clientes = threading.Lock()

    # ── Infraestrutura ────────────────────────────────────────────────────────

    def _post(self, url: str, call: str, param: dict) -> dict:
        payload = {
            "call":       call,
            "app_key":    self.app_key,
            "app_secret": self.app_secret,
            "param":      [param],
        }
        log.debug("    POST %s  call=%s", url, call)
        resp = self._session.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Vendedores — bulk ─────────────────────────────────────────────────────

    def _carregar_vendedores(self) -> None:
        """
        Tenta carregar vendedores do cache DB (TTL 24h).
        Se expirado ou vazio, busca via ListarVendedores e salva no banco.
        """
        import database
        cached = database.get_vendedores()
        if cached:
            self._cache_vendedores = cached
            log.info("  OMIE → %d vendedores carregados do cache DB.", len(cached))
            return

        log.info("  OMIE → cache de vendedores expirado, buscando na API…")
        pagina  = 1
        tot_pag = 1
        total   = 0

        while pagina <= tot_pag:
            try:
                data = self._post(self._URL_VENDEDORES, "ListarVendedores", {
                    "pagina":               pagina,
                    "registros_por_pagina": 100,
                })
                tot_pag = data.get("total_de_paginas", 1)
                for v in data.get("cadastro", []):
                    codigo = v.get("codigo")
                    nome   = str(v.get("nome") or "")
                    if codigo:
                        self._cache_vendedores[int(codigo)] = nome
                        total += 1
                log.debug("      vendedores: página %d/%d  acumulado=%d", pagina, tot_pag, total)
                pagina += 1
            except Exception as exc:
                log.warning("  Falha ao carregar vendedores (pág %d): %s", pagina, exc)
                break

        if self._cache_vendedores:
            database.set_vendedores(self._cache_vendedores)
        log.info("  OMIE → %d vendedores carregados da API.", total)

    def _carregar_empresas(self) -> None:
        """
        Tenta carregar clientes do cache DB (TTL 7 dias).
        Se expirado ou vazio, busca todos via ListarClientes paginado e salva no banco.
        ~8548 registros → ~171 páginas (50/página), roda uma vez a cada 7 dias.
        """
        import database
        cached = database.get_empresas()
        if cached:
            self._cache_empresas = cached
            log.info("  OMIE → %d clientes carregados do cache DB.", len(cached))
            return

        log.info("  OMIE → cache de clientes expirado, buscando todos na API…")
        pagina  = 1
        tot_pag = 1
        total   = 0

        while pagina <= tot_pag:
            try:
                data = self._post(self._URL_CLIENTES, "ListarClientes", {
                    "pagina":               pagina,
                    "registros_por_pagina": 50,
                })
                tot_pag = data.get("total_de_paginas", 1)
                for c in data.get("clientes_cadastro", []):
                    codigo = c.get("codigo_cliente_omie")
                    nome   = str(c.get("nome_fantasia") or "")
                    if codigo:
                        self._cache_empresas[int(codigo)] = nome
                        total += 1
                log.debug("      clientes: página %d/%d  acumulado=%d", pagina, tot_pag, total)
                pagina += 1
            except Exception as exc:
                log.warning("  Falha ao carregar clientes (pág %d): %s", pagina, exc)
                break

        if self._cache_empresas:
            database.set_empresas(self._cache_empresas)
        log.info("  OMIE → %d clientes carregados da API.", total)

    def nome_vendedor(self, codigo_vendedor: int) -> str:
        """Retorna o nome do vendedor direto do cache bulk. Sem chamada HTTP."""
        return self._cache_vendedores.get(codigo_vendedor, "")

    def consultar_cliente(self, codigo_cliente: int) -> str:
        """Retorna o nome da empresa direto do cache bulk. Sem chamada HTTP."""
        return self._cache_empresas.get(codigo_cliente, "")

    def listar_pedidos(self) -> list[dict]:
        """
        Lista todos os pedidos do período via ListarPedidos (paginado).
        Retorna lista bruta de pedido_venda_produto.
        Páginas com erro são puladas com log de aviso — não abortam a extração.
        """
        pedidos    = []
        pagina     = 1
        tot_pag    = 1
        erros_pag: list[int] = []

        while pagina <= tot_pag:
            try:
                data = self._post(self._URL_PEDIDOS, "ListarPedidos", {
                    "pagina":               pagina,
                    "registros_por_pagina": 100,
                    "filtrar_por_data_de":  config.MES_INICIO_OMIE,
                    "filtrar_por_data_ate": config.MES_FIM_OMIE,
                    "filtrar_apenas_inclusao": "N",
                })
                tot_pag = data.get("total_de_paginas", 1)
                pedidos.extend(data.get("pedido_venda_produto", []))
                log.debug("      pedidos: página %d/%d  acumulado=%d", pagina, tot_pag, len(pedidos))
            except Exception as exc:
                log.warning("  [AVISO] Falha ao buscar pedidos página %d: %s — continuando.", pagina, exc)
                erros_pag.append(pagina)
            pagina += 1

        if erros_pag:
            log.warning(
                "  OMIE pedidos: %d página(s) com erro (%s) — resultado pode estar incompleto.",
                len(erros_pag), erros_pag,
            )
        log.info("  OMIE → %d pedidos listados no período.", len(pedidos))
        return pedidos

    def listar_nfs(self) -> list[dict]:
        """
        Lista todas as NFs emitidas no período via ListarNF (resumo).
        Cada item contém: compl.nIdPedido, ide.nNF, ide.dEmi, total.ICMSTot.vNF.
        Páginas com erro são puladas com log de aviso — não abortam a extração.
        """
        nfs        = []
        pagina     = 1
        tot_pag    = 1
        erros_pag: list[int] = []

        while pagina <= tot_pag:
            try:
                data = self._post(self._URL_NF, "ListarNF", {
                    "dEmiInicial":          config.MES_INICIO_OMIE,
                    "dEmiFinal":            config.MES_FIM_OMIE,
                    "cApenasResumo":        "S",
                    "pagina":               pagina,
                    "registros_por_pagina": 50,
                })
                tot_pag = data.get("total_de_paginas", 1)
                nfs.extend(data.get("nfCadastro", []))
                log.debug("      NFs: página %d/%d  acumulado=%d", pagina, tot_pag, len(nfs))
            except Exception as exc:
                log.warning("  [AVISO] Falha ao buscar NFs página %d: %s — continuando.", pagina, exc)
                erros_pag.append(pagina)
            pagina += 1

        if erros_pag:
            log.warning(
                "  OMIE NFs: %d página(s) com erro (%s) — resultado pode estar incompleto.",
                len(erros_pag), erros_pag,
            )
        log.info("  OMIE → %d NFs listadas no período.", len(nfs))
        return nfs



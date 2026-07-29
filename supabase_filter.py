"""
supabase_filter.py
──────────────────
Consulta o Supabase para identificar NFs com manifestação inválida e remove
essas linhas do DataFrame antes de gerar as planilhas e o JSON do dashboard.

Manifestações consideradas VÁLIDAS (mantidas):
    'Operação Confirmada', 'Ciência da Operação', 'N/D', e nulo (None/vazio)

Qualquer outra manifestação indica problema (ex.: 'Desconhecimento da Operação',
'Operação não Realizada') e a NF correspondente é removida dos relatórios.
"""

import logging

import requests

import config

log = logging.getLogger(__name__)

# Manifestações que NÃO devem bloquear a NF (registros com estas são mantidos).
_MANIFESTACOES_VALIDAS = {
    "Operação Confirmada",
    "Ciência da Operação",
    "N/D",
}

_PAGE_SIZE = 1_000   # limite padrão do Supabase; paginamos manualmente


def _nf_para_chave(nf: str) -> str:
    """Remove zeros à esquerda — '00045678' e '45678' → '45678'."""
    try:
        return str(int(nf.strip()))
    except (ValueError, AttributeError):
        return nf.strip()


def _buscar_paginas(base_url: str, headers: dict) -> list[dict]:
    """
    Busca todos os registros da tabela paginando de _PAGE_SIZE em _PAGE_SIZE.
    O Supabase ignora limit > 1000 e retorna no máximo 1000 por requisição.
    """
    todos: list[dict] = []
    offset = 0

    while True:
        params = {
            "select": "numero_nf,manifestacao",
            "limit":  str(_PAGE_SIZE),
            "offset": str(offset),
        }
        resp = requests.get(base_url, headers=headers, params=params, timeout=30)
        log.debug(
            "  [SUPABASE] GET offset=%d → HTTP %d  (%d bytes)",
            offset, resp.status_code, len(resp.content),
        )
        resp.raise_for_status()
        pagina = resp.json()

        if not isinstance(pagina, list):
            log.error("  [SUPABASE] Resposta inesperada (não é lista): %s", str(pagina)[:200])
            break

        todos.extend(pagina)

        if len(pagina) < _PAGE_SIZE:
            break   # última página
        offset += _PAGE_SIZE

    return todos


def buscar_nfs_manifestacao_invalida() -> set[str]:
    """
    Retorna conjunto de chaves de NF cujas manifestações NÃO estão na lista válida.
    Pagina automaticamente para buscar todos os registros da tabela.
    Retorna conjunto vazio se as credenciais não estiverem configuradas ou a consulta falhar.
    """
    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        log.warning(
            "  [SUPABASE] SUPABASE_URL ou SUPABASE_KEY não configurados — "
            "filtro de manifestações ignorado."
        )
        return set()

    base_url = f"{config.SUPABASE_URL}/rest/v1/faturamento_consolidado"
    headers = {
        "apikey":         config.SUPABASE_KEY,
        "Authorization":  f"Bearer {config.SUPABASE_KEY}",
        "Accept-Profile": "faturamento",
    }

    try:
        rows = _buscar_paginas(base_url, headers)
    except Exception as exc:
        log.error("  [SUPABASE] Erro ao consultar faturamento_consolidado: %s", exc)
        return set()

    if not rows:
        log.warning(
            "  [SUPABASE] Tabela retornou 0 linhas. Causas prováveis:\n"
            "    1. SUPABASE_KEY está com a chave 'anon' — troque pela 'service_role' key\n"
            "       (RLS bloqueia a anon key; o SQL Editor usa role de admin e ignora RLS)\n"
            "    2. Schema 'faturamento' não está exposto nas API Settings do Supabase"
        )
        return set()

    nfs = {
        _nf_para_chave(str(r["numero_nf"]))
        for r in rows
        if r.get("numero_nf")
        and r.get("manifestacao") is not None
        and r.get("manifestacao") not in _MANIFESTACOES_VALIDAS
    }
    log.info(
        "  [SUPABASE] %d NF(s) com manifestação inválida encontrada(s) "
        "(de %d registros consultados).",
        len(nfs), len(rows),
    )
    if nfs:
        log.debug("  [SUPABASE] NFs inválidas (chaves): %s", sorted(nfs))
    return nfs


def filtrar_nfs_manifestadas(df):
    """
    Remove do DataFrame as linhas cujas NFs possuem manifestação inválida no Supabase.
    Afeta coordenador, vendedores e JSON (todos usam o mesmo df_coord).
    Retorna o DataFrame filtrado.
    """
    if df.empty or "Nota_Fiscal" not in df.columns:
        log.info("  [SUPABASE] DataFrame sem pedidos faturados — filtro de manifestações ignorado.")
        return df

    nfs_invalidas = buscar_nfs_manifestacao_invalida()
    if not nfs_invalidas:
        return df

    # Log das NFs do relatório atual para diagnóstico de não-match
    nfs_relatorio = set()
    for nota in df["Nota_Fiscal"].dropna():
        if nota not in ("-", ""):
            for nf in str(nota).split(" / "):
                nfs_relatorio.add(_nf_para_chave(nf))

    coincidencias = nfs_invalidas & nfs_relatorio
    log.debug(
        "  [SUPABASE] NFs do relatório atual: %d únicas. "
        "Interseção com inválidas: %s",
        len(nfs_relatorio),
        sorted(coincidencias) if coincidencias else "(nenhuma)",
    )

    def _tem_nf_invalida(nota_fiscal) -> bool:
        if not nota_fiscal or nota_fiscal in ("-", ""):
            return False
        for nf in str(nota_fiscal).split(" / "):
            if _nf_para_chave(nf) in nfs_invalidas:
                return True
        return False

    mascara = df["Nota_Fiscal"].apply(_tem_nf_invalida)
    n_removidos = int(mascara.sum())

    if n_removidos:
        log.info(
            "  [SUPABASE] %d pedido(s) removido(s) das planilhas e JSON "
            "por manifestação inválida:",
            n_removidos,
        )
        for _, row in df[mascara].iterrows():
            log.info(
                "    Pedido %s  NF %s  vendedor %s",
                row.get("Numero_Pedido", "?"),
                row.get("Nota_Fiscal", "?"),
                row.get("Nome_Vendedor", "?"),
            )
    else:
        log.info(
            "  [SUPABASE] Nenhum pedido do mês atual coincide com as NFs inválidas "
            "(as %d NFs inválidas são provavelmente de outros meses).",
            len(nfs_invalidas),
        )

    return df[~mascara].reset_index(drop=True)

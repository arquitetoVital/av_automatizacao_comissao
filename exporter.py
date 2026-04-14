"""
exporter.py
───────────
Transforma o DataFrame do coordenador em um JSON estruturado para o dashboard.

Colunas REMOVIDAS (não vão para o JSON):
  - Data_Venda
  - Numero_Pedido
  - Comissao_Vendedor_%
  - Comissao_Compras_%

Colunas MANTIDAS e renomeadas para camelCase:
  Nome_Cliente             → cliente
  Nome_Vendedor            → vendedor
  Valor_Pedido             → valorPedido
  Data_Nota_Fiscal         → dataNF
  Nota_Fiscal              → notaFiscal
  Valor_Faturado           → valorFaturado
  Valor_Pendente           → valorPendente
  Menor_Comissao_%         → comissaoPct
  Valor_Comissao_Calculado → valorComissao
  Obs_Comissao             → status
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)

# ── Colunas que NÃO devem ir para o JSON ─────────────────────────────────────
_COLUNAS_REMOVER = {
    "Data_Venda",
    "Numero_Pedido",
    "Comissao_Vendedor_%",
    "Comissao_Compras_%",
}

# ── Mapeamento das obs para status legível no frontend ────────────────────────
_STATUS_MAP: dict[str, str] = {
    "Comissao Definida!":                  "ok",
    "Comissao Definida! - Prejuizo":       "prejuizo",
    "Analise de Compras pendente!":        "pendente",
    "Ajuste a planilha de custo":          "erro",
    "Adicione o simulador na pasta CUSTO": "sem_simulador",
    "Pedido ainda nao faturado":           "sem_nf",
    "Fabricacao interna":                  "fabricacao_interna",
}

# ── Renomeação das colunas para camelCase ─────────────────────────────────────
_RENAME: dict[str, str] = {
    "Nome_Cliente":             "cliente",
    "Nome_Vendedor":            "vendedor",
    "Valor_Pedido":             "valorPedido",
    "Data_Nota_Fiscal":         "dataNF",
    "Nota_Fiscal":              "notaFiscal",
    "Valor_Faturado":           "valorFaturado",
    "Valor_Pendente":           "valorPendente",
    "Menor_Comissao_%":         "comissaoPct",
    "Valor_Comissao_Calculado": "valorComissao",
    "Obs_Comissao":             "status",
}


def _status_legivel(obs: str) -> str:
    """Converte obs_comissao para chave de status curta e consistente."""
    return _STATUS_MAP.get(str(obs).strip(), "desconhecido")


def _eh_faturado(row: pd.Series) -> bool:
    """Retorna True se o pedido possui Nota Fiscal emitida."""
    nf = str(row.get("Nota_Fiscal", "") or "").strip()
    return nf not in ("-", "")


def _resumo(df: pd.DataFrame) -> dict:
    """Calcula os totais gerais para o bloco resumo do JSON."""
    status_col    = df["Obs_Comissao"].fillna("").str.strip()
    mask_faturado = df.apply(_eh_faturado, axis=1)

    return {
        "totalPedidos":          int(len(df)),
        "totalPedidosFaturados":  int(mask_faturado.sum()),
        "totalPedidosAFaturar":   int((~mask_faturado).sum()),
        "valorTotalFaturado":    round(float(df["Valor_Faturado"].sum()), 2),
        "valorTotalAFaturar":    round(float(df.loc[~mask_faturado, "Valor_Pedido"].sum()), 2),
        "valorTotalComissao":    round(float(df["Valor_Comissao_Calculado"].sum()), 2),
        "pedidosSemSimulador":   int((status_col == "Adicione o simulador na pasta CUSTO").sum()),
        "pedidosEmErro":         int((status_col == "Ajuste a planilha de custo").sum()),
        "pedidosPendentes":      int((status_col == "Analise de Compras pendente!").sum()),
        "pedidosOk":             int(status_col.str.startswith("Comissao Definida").sum()),
    }


def _por_vendedor(df: pd.DataFrame) -> list[dict]:
    """Agrupa pedidos por vendedor com totais expandidos + lista de pedidos."""
    grupos: list[dict] = []

    for vendedor, df_v in df.groupby("Nome_Vendedor", sort=True):
        mask_fat = df_v.apply(_eh_faturado, axis=1)

        pedidos_lista = []
        for _, row in df_v.iterrows():
            pedido = {
                _RENAME.get(col, col): (
                    _status_legivel(row[col]) if col == "Obs_Comissao"
                    else (round(float(row[col]), 4) if isinstance(row[col], float) else row[col])
                )
                for col in df_v.columns
                if col not in _COLUNAS_REMOVER and col != "Nome_Vendedor"
            }
            pedidos_lista.append(pedido)

        grupos.append({
            "vendedor":               str(vendedor),
            "totalPedidos":           int(len(df_v)),
            "totalPedidosFaturados":  int(mask_fat.sum()),
            "totalPedidosAFaturar":   int((~mask_fat).sum()),
            "valorTotalFaturado":     round(float(df_v["Valor_Faturado"].sum()), 2),
            "valorTotalPendente":     round(float(df_v["Valor_Pendente"].sum()), 2),
            "valorTotalComissao":     round(float(df_v["Valor_Comissao_Calculado"].sum()), 2),
            "pedidos":                pedidos_lista,
        })

    return grupos


def gerar_json(df: pd.DataFrame) -> dict:
    """Converte o DataFrame do coordenador no dicionário do dashboard."""
    resumo  = _resumo(df)
    payload = {
        "mes":        config.MES_REF,
        "ano_mes":    config.ANO_MES_REF,
        "gerado_em":  datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "resumo":     resumo,
        "vendedores": _por_vendedor(df),
    }
    log.info(
        "  JSON: %d vendedores | %d pedidos (%d fat. / %d a fat.) | "
        "R$ %.2f faturado | R$ %.2f comissao",
        len(payload["vendedores"]),
        resumo["totalPedidos"],
        resumo["totalPedidosFaturados"],
        resumo["totalPedidosAFaturar"],
        resumo["valorTotalFaturado"],
        resumo["valorTotalComissao"],
    )
    return payload


def salvar_json_local(payload: dict, caminho: Path | None = None) -> Path:
    """Salva o JSON localmente como fallback. Padrao: {ANO_MES_REF}.json"""
    if caminho is None:
        caminho = Path(__file__).parent / f"{config.ANO_MES_REF}.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("  JSON salvo localmente: %s", caminho)
    return caminho

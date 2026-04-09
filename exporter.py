"""
exporter.py
───────────
Transforma o DataFrame do coordenador em um JSON estruturado para o dashboard.

Colunas REMOVIDAS (não vão para o JSON):
  - Data_Venda
  - Numero_Pedido
  - Comissao_Vendedor_%
  - Comissao_Compras_%
  - Obs_Comissao   (renomeada → status, com valor legível)

Colunas MANTIDAS e renomeadas para camelCase:
  Nome_Cliente            → cliente
  Nome_Vendedor           → vendedor
  Valor_Pedido            → valorPedido
  Data_Nota_Fiscal        → dataNF
  Nota_Fiscal             → notaFiscal
  Valor_Faturado          → valorFaturado
  Valor_Pendente          → valorPendente
  Menor_Comissao_%        → comissaoPct
  Valor_Comissao_Calculado→ valorComissao
  Obs_Comissao            → status

Estrutura do JSON gerado:
{
  "mes":       "04_ABRIL",
  "ano_mes":   "2026-04",
  "gerado_em": "2026-04-09T14:32:00",
  "resumo": {
    "total_pedidos":        307,
    "total_faturado":       1234567.89,
    "total_comissao":       12345.67,
    "pedidos_sem_nf":       12,
    "pedidos_sem_simulador":8,
    "pedidos_em_erro":      3,
    "pedidos_pendentes":    25,
    "pedidos_ok":           259
  },
  "vendedores": [
    {
      "vendedor":      "ISAQUE SANTOS",
      "totalPedidos":  10,
      "totalFaturado": 50000.00,
      "totalComissao": 650.00,
      "pedidos": [ { ... }, ... ]
    }
  ]
}
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)

# ── Colunas que NÃO devem ir para o JSON ──────────────────────────────────────
_COLUNAS_REMOVER = {
    "Data_Venda",
    "Numero_Pedido",
    "Comissao_Vendedor_%",
    "Comissao_Compras_%",
}

# ── Mapeamento das obs para status legível no frontend ────────────────────────
_STATUS_MAP: dict[str, str] = {
    "Comissao Definida!":               "ok",
    "Comissao Definida! - Prejuizo":    "prejuizo",
    "Analise de Compras pendente!":     "pendente",
    "Ajuste a planilha de custo":       "erro",
    "Adicione o simulador na pasta CUSTO": "sem_simulador",
    "Pedido ainda nao faturado":        "sem_nf",
}

# ── Renomeação das colunas para camelCase ────────────────────────────────────
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


def _resumo(df: pd.DataFrame) -> dict:
    """Calcula os totais gerais para o bloco 'resumo' do JSON."""
    status_col = df["Obs_Comissao"].fillna("").str.strip()

    return {
        "total_pedidos":           int(len(df)),
        "total_faturado":          round(float(df["Valor_Faturado"].sum()), 2),
        "total_comissao":          round(float(df["Valor_Comissao_Calculado"].sum()), 2),
        "pedidos_sem_nf":          int((status_col == "Pedido ainda nao faturado").sum()),
        "pedidos_sem_simulador":   int((status_col == "Adicione o simulador na pasta CUSTO").sum()),
        "pedidos_em_erro":         int((status_col == "Ajuste a planilha de custo").sum()),
        "pedidos_pendentes":       int((status_col == "Analise de Compras pendente!").sum()),
        "pedidos_ok":              int(status_col.str.startswith("Comissao Definida").sum()),
    }


def _por_vendedor(df: pd.DataFrame) -> list[dict]:
    """
    Agrupa pedidos por vendedor.
    Cada entrada contém os totais do vendedor + lista de pedidos individuais.
    """
    grupos: list[dict] = []

    for vendedor, df_v in df.groupby("Nome_Vendedor", sort=True):
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
            "vendedor":      str(vendedor),
            "totalPedidos":  int(len(df_v)),
            "totalFaturado": round(float(df_v["Valor_Faturado"].sum()), 2),
            "totalComissao": round(float(df_v["Valor_Comissao_Calculado"].sum()), 2),
            "pedidos":       pedidos_lista,
        })

    return grupos


def gerar_json(df: pd.DataFrame) -> dict:
    """
    Converte o DataFrame completo do coordenador no dicionário do dashboard.
    Chamado pelo main.py antes do commit no GitHub.
    """
    payload = {
        "mes":       config.MES_REF,
        "ano_mes":   config.ANO_MES_REF,
        "gerado_em": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "resumo":    _resumo(df),
        "vendedores": _por_vendedor(df),
    }
    log.info(
        "  JSON gerado: %d vendedores | %d pedidos | R$ %.2f faturado",
        len(payload["vendedores"]),
        payload["resumo"]["total_pedidos"],
        payload["resumo"]["total_faturado"],
    )
    return payload


def salvar_json_local(payload: dict, caminho: Path | None = None) -> Path:
    """
    Salva o JSON localmente (útil para debug ou fallback).
    Padrão: mesmo diretório do script, nome {ANO_MES_REF}.json
    """
    if caminho is None:
        caminho = Path(__file__).parent / f"{config.ANO_MES_REF}.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("  JSON salvo localmente: %s", caminho)
    return caminho

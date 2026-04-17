"""
models.py
─────────
Estruturas de dados do domínio.
Sem dependências de libs externas — apenas stdlib.
"""

from dataclasses import dataclass


@dataclass
class Pedido:
    """Representa um pedido de venda OMIE."""

    data_venda:       str
    numero_pedido:    str
    nome_cliente:     str
    nome_vendedor:    str
    valor_pedido:     float
    data_nota_fiscal: str    # DD/MM/YYYY ou "-"
    nota_fiscal:      str    # número(s) da NF ou "-"
    valor_faturado:   float
    valor_pendente:   float

    # Comissão proposta pelo vendedor (simulador da pasta VENDEDOR)
    comissao_vendedor_pct:   float = 0.0

    # Comissão revisada pelo comprador (simulador da pasta COMPRADOR)
    comissao_compras_pct:    float = 0.0

    # Comissão menor entre as duas — base de pagamento
    comissao_menor_pct:      float = 0.0
    valor_comissao_menor:    float = 0.0

    obs_comissao: str = ""

    # Flag de cor vermelha nos relatórios (simulador rejeitado pelo comprador)
    em_erro: bool = False

    # Flag interna: True quando a comissão foi definida pela planilha comissoes_fixas.xlsx.
    # Esses pedidos já têm comissao_compras_pct preenchida e devem seguir a lógica
    # "menor entre vendedor e compras" quando o vendedor adicionar simulador.
    comissao_fixa: bool = False

    def to_dict(self) -> dict:
        """Converte para dicionário com os nomes de coluna usados no Excel/API."""
        return {
            "Data_Venda":               self.data_venda,
            "Numero_Pedido":            self.numero_pedido,
            "Nome_Cliente":             self.nome_cliente,
            "Nome_Vendedor":            self.nome_vendedor,
            "Valor_Pedido":             self.valor_pedido,
            "Data_Nota_Fiscal":         self.data_nota_fiscal,
            "Nota_Fiscal":              self.nota_fiscal,
            "Valor_Faturado":           self.valor_faturado,
            "Valor_Pendente":           self.valor_pendente,
            "Comissao_Vendedor_%":      self.comissao_vendedor_pct,
            "Comissao_Compras_%":       self.comissao_compras_pct,
            "Menor_Comissao_%":         self.comissao_menor_pct,
            "Valor_Comissao_Calculado": self.valor_comissao_menor,
            "Obs_Comissao":             self.obs_comissao,
        }


@dataclass
class InfoCusto:
    """Dados extraídos de uma planilha de simulador de custo."""

    id_pedido: str
    letra_com: str | None    # célula Z5
    status:    str | None = None   # célula AB12 — "Prejuízo" zera comissão

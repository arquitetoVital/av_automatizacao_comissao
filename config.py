"""
config.py
─────────
Único lugar para alterar datas e constantes do projeto.
Nenhum outro módulo define valores de configuração — apenas importa daqui.

Credenciais sensíveis são lidas de variáveis de ambiente (ou de um arquivo .env).
Para desenvolvimento local, crie um arquivo .env na raiz do projeto:

    OMIE_APP_KEY=4011885988110
    OMIE_APP_SECRET=415133ab4e1db4cf532665301496e0f3

Jamais commite o arquivo .env no repositório.
"""

import os
from calendar import monthrange
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════
LOG_FILE          = Path("gerar_comissoes.log")
LOG_SUBSTITUICOES = Path("substituicoes_simuladores.log")

# ═══════════════════════════════════════════════════════
#  OMIE — credenciais via variável de ambiente
# ═══════════════════════════════════════════════════════
OMIE_APP_KEY    = os.environ["OMIE_APP_KEY"]
OMIE_APP_SECRET = os.environ["OMIE_APP_SECRET"]

# ═══════════════════════════════════════════════════════
#  MÊS DE REFERÊNCIA
# ═══════════════════════════════════════════════════════
#
#  MODO_AUTO=true  → usa o mês corrente automaticamente (ideal para agendador)
#  MODO_AUTO=false → usa a data definida em _MES_MANUAL  (ideal para reprocessar meses passados)
#
#  Para alternar via variável de ambiente (ou .env):
#    MODO_AUTO=true
#    MODO_AUTO=false
#
_MODO_AUTO  = os.getenv("MODO_AUTO", "true").lower() == "true"
MODO_TESTE  = os.getenv("MODO_TESTE", "false").lower() == "true"
_MES_MANUAL = date(2026, 5, 1)   # ← editar apenas quando MODO_AUTO=false
_MES        = date.today().replace(day=1) if _MODO_AUTO else _MES_MANUAL

_MESES_PT = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO",    4: "ABRIL",
    5: "MAIO",    6: "JUNHO",     7: "JULHO",     8: "AGOSTO",
    9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO",
}
_ultimo_dia = monthrange(_MES.year, _MES.month)[1]

MES_REF         = f"{_MES.month:02d}_{_MESES_PT[_MES.month]}"       # "03_MARÇO"
MES_INICIO_OMIE = _MES.strftime("01/%m/%Y")                          # "01/03/2026"
MES_FIM_OMIE    = f"{_ultimo_dia:02d}/{_MES.month:02d}/{_MES.year}"  # "31/03/2026"
ANO_MES_REF     = _MES.strftime("%Y-%m")                             # "2026-03"
PASTA_CUSTO     = f"CUSTO {_MESES_PT[_MES.month]}"                   # "CUSTO MARÇO"

# ═══════════════════════════════════════════════════════
#  PASTAS DE REDE — sobrescrevíveis via variável de ambiente
#
#  PASTA_VENDEDOR_SP → simuladores dos vendedores SP + relatórios individuais SP
#  PASTA_VENDEDOR_MG → simuladores dos vendedores MG + relatórios individuais MG
# ═══════════════════════════════════════════════════════
_BASE = Path(os.getenv("PASTA_BASE", r"Z:\TI\ROBERT\PROJETO COMISSOES"))

PASTA_COORD       = Path(os.getenv("PASTA_COORD",       str(_BASE / "TESTE_COORDENADOR")))
PASTA_COORD_MG    = Path(os.getenv("PASTA_COORD_MG",    str(_BASE / "TESTE_COORDENADOR_MG")))
PASTA_VENDEDOR_SP = Path(os.getenv("PASTA_VENDEDOR_SP", str(_BASE / "PASTA_VENDEDOR_SP")))
PASTA_VENDEDOR_MG = Path(os.getenv("PASTA_VENDEDOR_MG", str(_BASE / "PASTA_VENDEDOR_MG")))
PASTA_COMPRADOR   = Path(os.getenv("PASTA_COMPRADOR",   str(_BASE / "TESTE_COMPRADOR")))
PASTA_COMPRADOR_MG = Path(os.getenv("PASTA_COMPRADOR_MG", str(_BASE / "TESTE_COMPRADOR_MG")))


# ═══════════════════════════════════════════════════════
#  ANALISTA DE VENDAS
#
#  RELATORIO_ANALISTA_ATIVO = True  → ativa TODAS as funções da analista:
#    - Cópia do relatório do coordenador em PASTA_ANALISTA
#    - Cópia dos simuladores em PASTA_ANALISTA_SIMULADORES / MES_REF / {SP|MG} /
#  RELATORIO_ANALISTA_ATIVO = False → passos ignorados (padrão)
#
#  Para ativar via variável de ambiente:
#    RELATORIO_ANALISTA_ATIVO=true
#    PASTA_ANALISTA=Z:\CAMINHO\RELATORIO_ANALISTA
#    PASTA_ANALISTA_SIMULADORES=Z:\CAMINHO\SIMULADORES_ANALISTA
# ═══════════════════════════════════════════════════════
RELATORIO_ANALISTA_ATIVO     = os.getenv("RELATORIO_ANALISTA_ATIVO", "false").lower() == "true"
PASTA_ANALISTA               = Path(os.getenv("PASTA_ANALISTA",             str(_BASE / "ANALISTA_VENDAS")))
PASTA_ANALISTA_SIMULADORES   = Path(os.getenv("PASTA_ANALISTA_SIMULADORES", str(_BASE / "ANALISTA_VENDAS" / "SIMULADORES")))

# ═══════════════════════════════════════════════════════
#  PLANILHA DE BLOQUEIO DE NFs — ANALISE_SIMULADOR.xlsx
#
#  Preenchida pela analista com duas colunas:
#    "NF bloqueada"     → número da NF a ser bloqueada
#    "Motivo do Bloqueio" → texto exibido apenas no relatório do coordenador
#
#  BLOQUEIO_NF_ATIVO=true  → zera comissão E exibe motivo (padrão)
#  BLOQUEIO_NF_ATIVO=false → apenas exibe motivo; comissão permanece calculada
#
#  Para alterar o caminho via variável de ambiente:
#    ANALISE_SIMULADOR_PATH=Z:\CAMINHO\ANALISE_SIMULADOR.xlsx
# ═══════════════════════════════════════════════════════
BLOQUEIO_NF_ATIVO      = os.getenv("BLOQUEIO_NF_ATIVO", "true").lower() == "true"
ANALISE_SIMULADOR_PATH = Path(os.getenv(
    "ANALISE_SIMULADOR_PATH",
    str(_BASE / "ANALISE_SIMULADOR.xlsx"),
))

# ═══════════════════════════════════════════════════════
#  SUPABASE — credenciais via variável de ambiente
#
#  Usadas pelo supabase_filter.py para consultar NFs com
#  manifestação inválida e removê-las dos relatórios.
#
#  SUPABASE_URL=https://seu-projeto.supabase.co
#  SUPABASE_KEY=sua_service_role_key
# ═══════════════════════════════════════════════════════
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ═══════════════════════════════════════════════════════
#  REGRAS DE COMISSÃO
# ═══════════════════════════════════════════════════════
TABELA_COMISSAO: dict[str, float] = {
    "A": 0.020,
    "B": 0.013,
    "C": 0.007,
    "D": 0.005,
}

"""
reports.py
──────────
Geração dos arquivos Excel de comissão.
Recebe DataFrames prontos — sem acoplamento com services.
"""

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
from utils import nome_para_pasta

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  LISTAS DE FILIAIS
# ═══════════════════════════════════════════════════════

def _carregar_lista(nome_arquivo: str) -> set[str]:
    """
    Lê um arquivo de lista de vendedores (mesmo formato da blacklist).
    Retorna conjunto de nomes no formato de pasta (underscores, maiúsculo).
    """
    caminho = Path(__file__).parent / nome_arquivo
    if not caminho.exists():
        log.warning("  %s não encontrado — nenhum vendedor classificado por ele.", nome_arquivo)
        return set()

    nomes: set[str] = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            nomes.add(linha.upper())

    log.info("  %s carregado: %d entradas.", nome_arquivo, len(nomes))
    return nomes


# ═══════════════════════════════════════════════════════
#  LAYOUT DO EXCEL
# ═══════════════════════════════════════════════════════

# Coordenador — visão completa com comparativo
COLUNAS_COORD = [
    "Data_Venda", "Numero_Pedido", "Nome_Cliente", "Nome_Vendedor",
    "Valor_Pedido", "Data_Nota_Fiscal", "Nota_Fiscal",
    "Valor_Faturado", "Valor_Pendente",
    "Comissao_Vendedor_%", "Comissao_Compras_%", "Menor_Comissao_%",
    "Valor_Comissao_Calculado",
    "Obs_Comissao",
]
LARGURAS_COORD = [14, 16, 32, 28, 14, 18, 14, 15, 15, 20, 18, 16, 22, 40]

# Vendedor — visão simplificada sem comparativo
COLUNAS_VENDOR = [
    "Data_Venda", "Numero_Pedido", "Nome_Cliente", "Nome_Vendedor",
    "Valor_Pedido", "Data_Nota_Fiscal", "Nota_Fiscal",
    "Valor_Faturado", "Valor_Pendente",
    "Comissao_Definida_%", "Valor_Comissao_Calculado",
    "Obs_Comissao",
]
LARGURAS_VENDOR = [14, 16, 32, 28, 14, 18, 14, 15, 15, 18, 22, 40]

COLUNAS_MOEDA = {"Valor_Pedido", "Valor_Faturado", "Valor_Pendente", "Valor_Comissao_Calculado"}
COLUNAS_PCT   = {"Comissao_Vendedor_%", "Comissao_Compras_%", "Menor_Comissao_%", "Comissao_Definida_%"}

COR_HEADER  = "1F4E79"
COR_LINHA_A = "E2EFDA"   # linhas pares
COR_LINHA_B = "F2F2F2"   # linhas ímpares
COR_LINHA_ERRO = "FFCCCC"  # vermelho claro — pedidos com planilha rejeitada (melhoria 4e)

_OBS_ERRO = "Ajuste a planilha de custo"   # valor exato definido em services.py

FMT_MOEDA = "R$ #,##0.00"
FMT_PCT   = "0.00%"


# ═══════════════════════════════════════════════════════
#  ESCRITA DO EXCEL
# ═══════════════════════════════════════════════════════

def _escrever_excel(df: pd.DataFrame, caminho: Path, colunas: list, larguras: list) -> None:
    """Gera .xlsx em temp local e copia para o destino. Salva com timestamp se bloqueado."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendas"

    font_h  = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    fill_h  = PatternFill("solid", start_color=COR_HEADER)
    align_c = Alignment(horizontal="center", vertical="center", wrap_text=True)
    borda   = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"),  bottom=Side(style="thin"),
    )

    for col_idx, (col_name, larg) in enumerate(zip(colunas, larguras), start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name.replace("_", " "))
        cell.font      = font_h
        cell.fill      = fill_h
        cell.alignment = align_c
        cell.border    = borda
        ws.column_dimensions[get_column_letter(col_idx)].width = larg

    ws.row_dimensions[1].height = 28
    font_d = Font(name="Arial", size=9)

    df_out = df[colunas].copy()
    for r_idx, row in enumerate(df_out.itertuples(index=False), start=2):
        # Melhoria 4e: linha vermelha se planilha está na pasta ERRO
        obs_val = ""
        if "Obs_Comissao" in colunas:
            obs_idx = colunas.index("Obs_Comissao")
            obs_val = str(row[obs_idx]) if row[obs_idx] else ""

        if obs_val.strip() == _OBS_ERRO:
            cor = COR_LINHA_ERRO
        else:
            cor = COR_LINHA_A if r_idx % 2 == 0 else COR_LINHA_B

        fill_d = PatternFill("solid", start_color=cor)

        for c_idx, value in enumerate(row, start=1):
            cell           = ws.cell(row=r_idx, column=c_idx, value=value)
            cell.font      = font_d
            cell.fill      = fill_d
            cell.border    = borda
            cell.alignment = Alignment(vertical="center")

            col_name = colunas[c_idx - 1]
            if col_name in COLUNAS_MOEDA:
                cell.number_format = FMT_MOEDA
            elif col_name in COLUNAS_PCT:
                cell.number_format = FMT_PCT

    ws.freeze_panes = "A2"

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        wb.save(tmp_path)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path, caminho)
        log.info("  Arquivo salvo: %s  (%d linhas)", caminho, len(df_out))
    except PermissionError:
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = caminho.with_stem(f"{caminho.stem}_{ts}")
        shutil.copy2(tmp_path, fallback)
        log.warning("  '%s' bloqueado — salvo como: %s", caminho.name, fallback)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════

def _nome_abreviado(nome_vendedor: str) -> str:
    """
    Retorna primeiro e último nome para uso no nome do arquivo.
    'ISAQUE RODRIGUES DOS SANTOS' → 'ISAQUE_SANTOS'
    'ISAQUE SANTOS'               → 'ISAQUE_SANTOS'
    'ISAQUE'                      → 'ISAQUE'
    """
    partes = nome_vendedor.strip().split()
    if len(partes) <= 1:
        abrev = partes[0] if partes else nome_vendedor
    else:
        abrev = f"{partes[0]}_{partes[-1]}"
    return "".join(c for c in abrev if c not in r'\/:*?"<>|')


def _pasta_filial(nome_vendedor: str, lista_sp: set[str], lista_mg: set[str]) -> Path | None:
    """
    Retorna a pasta de destino do relatório do vendedor conforme sua filial.
    Retorna None se o vendedor não estiver em nenhuma das listas.
    """
    chave = nome_para_pasta(nome_vendedor).upper()
    if chave in lista_sp:
        return config.PASTA_VENDEDOR_SP
    if chave in lista_mg:
        return config.PASTA_VENDEDOR_MG
    return None


# ═══════════════════════════════════════════════════════
#  INTERFACE PÚBLICA
# ═══════════════════════════════════════════════════════

def gerar_relatorio_coordenador(df: pd.DataFrame) -> None:
    """Gera o relatório consolidado para o coordenador."""
    log.info("══ Salvando planilha do coordenador ══")
    caminho = config.PASTA_COORD / f"{config.MES_REF}_RELATORIO_GERAL_COMISSAO.xlsx"
    _escrever_excel(df, caminho, COLUNAS_COORD, LARGURAS_COORD)


def distribuir_para_vendedores(df: pd.DataFrame) -> None:
    """
    Gera uma planilha individual por vendedor na pasta de rede da filial correta.
    Vendedores não classificados em nenhuma lista são ignorados com aviso no log.
    """
    log.info("══ Distribuindo planilhas individuais ══")

    df = df.copy()
    df["Comissao_Definida_%"] = df["Menor_Comissao_%"]

    lista_sp = _carregar_lista("vendedores_sp.txt")
    lista_mg = _carregar_lista("vendedores_mg.txt")

    vendedores = df["Nome_Vendedor"].dropna().unique()
    log.info("  Vendedores: %d", len(vendedores))

    nao_classificados: list[str] = []

    for vendedor in sorted(vendedores):
        pasta_base = _pasta_filial(vendedor, lista_sp, lista_mg)

        if pasta_base is None:
            nao_classificados.append(vendedor)
            continue

        df_v       = df[df["Nome_Vendedor"] == vendedor].copy()
        nome_pasta = nome_para_pasta(vendedor)
        nome_arq   = _nome_abreviado(vendedor)
        destino    = (
            pasta_base
            / nome_pasta
            / config.MES_REF
            / f"{config.MES_REF}_COMISSAO_{nome_arq}.xlsx"
        )
        log.info("  '%s' → %d pedidos  [%s]", vendedor, len(df_v), pasta_base.parts[-1])
        _escrever_excel(df_v, destino, COLUNAS_VENDOR, LARGURAS_VENDOR)

    if nao_classificados:
        log.warning(
            "  [AVISO] %d vendedor(es) sem filial definida — sem relatório gerado:",
            len(nao_classificados),
        )
        for nome in nao_classificados:
            log.warning("    – %s", nome)

    log.info("  Distribuição concluída.")

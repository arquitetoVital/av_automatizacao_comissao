"""
services.py
───────────
Toda a lógica de negócio do sistema de comissões.

Melhorias implementadas:
  #1  Pasta do coordenador: subpastas OK e ERRO criadas automaticamente
  #2  Pasta CUSTO criada vazia na pasta do mes do vendedor se nao existir
  #3  Decisao de copia baseada em pasta (nao em nome de arquivo)
  #4  Obs por situacao: sem NF, sem simulador, ERRO, pendente
  #5  Flag em_erro para colorir a linha em vermelho nos relatorios
"""

import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import xml.etree.ElementTree as ET
import zipfile
import pandas as pd

import openpyxl

import config
import database
from clients import OmieClient
from models import InfoCusto, Pedido
from utils import nome_para_pasta

log = logging.getLogger(__name__)

_THREADS_SIMULADORES = 4
_EXT_SIMULADOR = {'.xlsx', '.xlsm'}


# ═══ UTILITÁRIOS DE DATA ═══════════════════════════════

def _normalizar_data(valor) -> str:
    if valor is None or str(valor).strip() in ("", "-"):
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    s = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return s


# ═══ BLACKLIST ═════════════════════════════════════════

def _carregar_blacklist() -> set[str]:
    caminho = Path(__file__).parent / "blacklist.txt"
    if not caminho.exists():
        log.warning("  blacklist.txt nao encontrado.")
        return set()
    nomes: set[str] = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            nomes.add(linha.upper())
    log.info("  Blacklist carregada: %d entradas.", len(nomes))
    return nomes


def _na_blacklist(nome_vendedor: str, blacklist: set[str]) -> bool:
    return nome_para_pasta(nome_vendedor).upper() in blacklist


# ═══ BLACKLIST DE CLIENTES ════════════════════════════

def _normalizar_str(s: str) -> str:
    """Remove acentos e converte para maiúsculo para comparação normalizada."""
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()


def _carregar_blacklist_clientes() -> list[str]:
    """
    Lê blacklist_clientes.txt e retorna lista de termos normalizados.
    Comparação por SUBSTRING — um termo contido no nome bloqueia o cliente.
    Ex: "ACOS VITAL" bloqueia "ACOS VITAL CHILE LTDA", "ACOS VITAL S.A.", etc.
    """
    caminho = Path(__file__).parent / "blacklist_clientes.txt"
    if not caminho.exists():
        log.warning("  blacklist_clientes.txt nao encontrado — nenhum cliente ignorado.")
        return []
    termos: list[str] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            termos.append(_normalizar_str(linha))
    log.info("  Blacklist clientes: %d termo(s) carregado(s).", len(termos))
    return termos


def _cliente_bloqueado(nome_cliente: str, blacklist_clientes: list[str]) -> bool:
    """Retorna True se algum termo da blacklist for substring do nome do cliente."""
    nome_norm = _normalizar_str(nome_cliente)
    return any(termo in nome_norm for termo in blacklist_clientes)


# ═══ COMISSÕES FIXAS POR CLIENTE ═══════════════════════

def carregar_comissoes_fixas() -> dict[str, float]:
    """
    Lê comissoes_fixas.xlsx (colunas CLIENTE | COMISSAO PADRÃO) e retorna
    um dicionário {nome_cliente_normalizado: percentual_float}.

    Exemplos de valor aceito na coluna COMISSAO PADRÃO:
      "2%"  → 0.02     "1,3%" → 0.013    "0.7%" → 0.007    "0.02" → 0.02

    O arquivo deve estar na mesma pasta do script.
    Se não existir ou estiver vazio, retorna {} sem interromper a execução.
    """
    caminho = Path(__file__).parent / "comissoes_fixas.xlsx"
    if not caminho.exists():
        log.warning("  comissoes_fixas.xlsx nao encontrado — nenhuma comissao fixa aplicada.")
        return {}

    fixas: dict[str, float] = {}
    try:
        wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
        ws = wb.active
        cabecalho_pulado = False
        for row in ws.iter_rows(values_only=True):
            if not cabecalho_pulado:
                cabecalho_pulado = True
                continue
            if not row or row[0] is None:
                continue
            nome_raw  = str(row[0]).strip()
            valor_raw = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            if not nome_raw or not valor_raw:
                continue
            # Normaliza percentual: "2%" → 0.02 | "1,3%" → 0.013 | "0.02" → 0.02
            valor_str = valor_raw.replace("%", "").replace(",", ".").strip()
            try:
                pct = float(valor_str)
                if pct > 1:       # veio como 2.0 em vez de 0.02
                    pct = pct / 100
                fixas[_normalizar_str(nome_raw)] = pct
            except ValueError:
                log.warning("  [COMISSAO-FIXA] Valor inválido para '%s': '%s'", nome_raw, valor_raw)
        wb.close()
    except Exception as exc:
        log.error("  [COMISSAO-FIXA] Erro ao ler '%s': %s", caminho, exc)
        return {}

    log.info("  Comissoes fixas carregadas: %d cliente(s).", len(fixas))
    return fixas


def _aplicar_comissoes_fixas(
    pedidos: list,
    fixas: dict[str, float],
) -> None:
    """
    Para cada pedido cujo cliente consta em `fixas`:
      - Define comissao_compras_pct com o percentual da planilha.
      - Define comissao_fixa = True (sinaliza que compras já está definida).
      - Se o pedido já tiver NF mas ainda não tiver simulador do vendedor,
        aplica o percentual também em comissao_menor_pct / valor_comissao_menor
        como estimativa (obs = "Fabricacao interna / simulador ausente").
        Quando o vendedor adicionar o simulador, calcular_comissoes() vai
        comparar e escolher o menor normalmente.
    """
    if not fixas:
        return
    aplicados = 0
    for p in pedidos:
        chave = _normalizar_str(p.nome_cliente)
        pct = fixas.get(chave)
        if pct is None:
            continue
        p.comissao_compras_pct = pct
        p.comissao_fixa        = True
        aplicados += 1

    if aplicados:
        log.info("  Comissoes fixas aplicadas em %d pedido(s).", aplicados)



# ═══ EXTRAÇÃO OMIE ═════════════════════════════════════

def _agrupar_nfs_por_pedido(nfs: list[dict]) -> dict[int, dict]:
    agrupado: dict[int, dict] = {}
    for nf in nfs:
        compl = nf.get("compl", {})
        ide   = nf.get("ide",   {})
        total = nf.get("total", {}).get("ICMSTot", {})
        n_id_pedido = compl.get("nIdPedido")
        if not n_id_pedido:
            continue
        n_nf  = str(ide.get("nNF", "") or "")
        d_emi = _normalizar_data(ide.get("dEmi", ""))
        v_nf  = float(total.get("vNF", 0) or 0)
        if n_id_pedido not in agrupado:
            agrupado[n_id_pedido] = {"notas": [], "datas_nf": [], "valor_faturado": 0.0}
        g = agrupado[n_id_pedido]
        if n_nf and n_nf not in g["notas"]:
            g["notas"].append(n_nf)
        if d_emi and d_emi != "-" and d_emi not in g["datas_nf"]:
            g["datas_nf"].append(d_emi)
        g["valor_faturado"] = round(g["valor_faturado"] + v_nf, 2)
    return agrupado


def _pedido_excluido(info: dict) -> tuple[bool, str]:
    if info.get("cancelado") == "S":
        return True, "cancelado"
    if info.get("devolvido") == "S":
        return True, "devolvido"
    if info.get("denegado") == "S":
        return True, "denegado"
    return False, ""


def _data_no_mes(data_str: str) -> bool:
    try:
        d   = datetime.strptime(data_str, "%d/%m/%Y")
        ini = datetime.strptime(config.MES_INICIO_OMIE, "%d/%m/%Y")
        fim = datetime.strptime(config.MES_FIM_OMIE,    "%d/%m/%Y")
        return ini <= d <= fim
    except Exception:
        return False


def extrair_omie() -> list[Pedido]:
    log.info("── Extraindo dados OMIE ──")
    client    = OmieClient()
    blacklist          = _carregar_blacklist()
    blacklist_clientes = _carregar_blacklist_clientes()

    pedidos_raw = client.listar_pedidos()
    idx_pedidos: dict[int, dict] = {}
    excluidos = 0
    for p in pedidos_raw:
        cab    = p.get("cabecalho", {})
        info   = p.get("infoCadastro", {})
        codigo = cab.get("codigo_pedido")
        if not codigo:
            continue
        excluido, motivo = _pedido_excluido(info)
        if excluido:
            log.debug("    [SKIP] Pedido %s %s.", cab.get("numero_pedido", ""), motivo)
            excluidos += 1
            continue
        idx_pedidos[codigo] = p
    log.info("  %d pedidos indexados (%d excluidos).", len(idx_pedidos), excluidos)

    nfs      = client.listar_nfs()
    agrupado = _agrupar_nfs_por_pedido(nfs)
    log.info("  %d NFs -> %d pedidos unicos com NF.", len(nfs), len(agrupado))

    pedidos:       list[Pedido] = []
    blacklistados  = 0
    nfs_sem_pedido = 0
    ids_inseridos: set[str] = set()

    def _montar_pedido(pedido_raw: dict, grupo: dict | None) -> Pedido | None:
        nonlocal blacklistados
        cab  = pedido_raw.get("cabecalho", {})
        tot  = pedido_raw.get("total_pedido", {})
        adic = pedido_raw.get("informacoes_adicionais", {})
        info = pedido_raw.get("infoCadastro", {})

        numero_pedido   = str(cab.get("numero_pedido", ""))
        codigo_cliente  = cab.get("codigo_cliente")
        codigo_vendedor = adic.get("codVend")
        valor_pedido    = float(tot.get("valor_total_pedido", 0) or 0)
        data_venda      = _normalizar_data(info.get("dInc", ""))

        nome_cliente  = client.consultar_cliente(int(codigo_cliente))  if codigo_cliente  else ""
        nome_vendedor = client.nome_vendedor(int(codigo_vendedor))     if codigo_vendedor else ""

        if not nome_vendedor.strip():
            log.debug("    [SKIP] Pedido %s sem vendedor.", numero_pedido)
            blacklistados += 1
            return None
        if _na_blacklist(nome_vendedor, blacklist):
            log.debug("    [BLACKLIST] Pedido %s (%s).", numero_pedido, nome_vendedor)
            blacklistados += 1
            return None

        if _cliente_bloqueado(nome_cliente, blacklist_clientes):
            log.debug(
                "    [BLACKLIST-CLIENTE] Pedido %s ignorado (cliente: %s).",
                numero_pedido, nome_cliente,
            )
            blacklistados += 1
            return None

        if grupo:
            valor_faturado = grupo["valor_faturado"]
            notas          = " / ".join(sorted(grupo["notas"]))    or "-"
            datas_nf       = " / ".join(sorted(grupo["datas_nf"])) or "-"
        else:
            valor_faturado = 0.0
            notas          = "-"
            datas_nf       = "-"

        valor_pendente = round(valor_pedido - valor_faturado, 2)
        if grupo and valor_faturado > valor_pedido:
            log.warning(
                "    [ALERTA] Pedido %s: faturado (%.2f) > pedido (%.2f) NFs: %s",
                numero_pedido, valor_faturado, valor_pedido, notas,
            )
        return Pedido(
            data_venda=data_venda, numero_pedido=numero_pedido,
            nome_cliente=nome_cliente, nome_vendedor=nome_vendedor,
            valor_pedido=valor_pedido, data_nota_fiscal=datas_nf,
            nota_fiscal=notas, valor_faturado=valor_faturado,
            valor_pendente=valor_pendente,
        )

    for n_id_pedido, grupo in agrupado.items():
        pedido_raw = idx_pedidos.get(n_id_pedido)
        if pedido_raw is None:
            nfs_sem_pedido += 1
            continue
        p = _montar_pedido(pedido_raw, grupo)
        if p:
            pedidos.append(p)
            ids_inseridos.add(p.numero_pedido)

    ids_com_nf = {
        str(idx_pedidos[n]["cabecalho"]["numero_pedido"])
        for n in agrupado if n in idx_pedidos
    }
    sem_nf_incluidos = 0
    for pedido_raw in idx_pedidos.values():
        info = pedido_raw.get("infoCadastro", {})
        cab  = pedido_raw.get("cabecalho", {})
        num  = str(cab.get("numero_pedido", ""))
        if num in ids_com_nf:
            continue
        data_inc = _normalizar_data(info.get("dInc", ""))
        if not _data_no_mes(data_inc):
            continue
        p = _montar_pedido(pedido_raw, None)
        if p:
            pedidos.append(p)
            sem_nf_incluidos += 1

    pedidos.sort(key=lambda p: p.numero_pedido.zfill(10))
    log.info(
        "  OMIE: %d com NF | %d sem NF | %d blacklistados (vendedor+cliente) | %d NFs sem pedido.",
        len(ids_inseridos), sem_nf_incluidos, blacklistados, nfs_sem_pedido,
    )
    database.upsert_pedidos(pedidos, config.ANO_MES_REF)
    database.registrar_sync("OMIE")
    return pedidos


# ═══ LEITURA DE SIMULADORES ════════════════════════════

def _para_float(valor) -> float:
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    if not s or s.startswith("#"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _is_prejuizo(status) -> bool:
    return str(status or "").strip().upper() == "PREJUIZO"


def _extrair_id_pedido(stem: str) -> str | None:
    for sufixo in (" OK", "_OK"):
        if stem.upper().endswith(sufixo.upper()):
            stem = stem[: -len(sufixo)]
            break
    nums = re.findall(r"\d{3,}", stem)
    return nums[-1].zfill(6) if nums else None


_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CELULAS_ALVO = {"Z5", "AB12"}


def _carregar_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall(f"{{{_NS}}}si"):
        t = si.find(f"{{{_NS}}}t")
        if t is not None:
            strings.append(t.text or "")
        else:
            partes = [r.find(f"{{{_NS}}}t") for r in si.findall(f"{{{_NS}}}r")]
            strings.append("".join(p.text or "" for p in partes if p is not None))
    return strings


def _resolver_valor(c_elem, shared: list[str]):
    v = c_elem.find(f"{{{_NS}}}v")
    if v is None or v.text is None:
        return None
    tipo = c_elem.get("t", "")
    if tipo == "s":
        return shared[int(v.text)]
    if tipo == "b":
        return bool(int(v.text))
    try:
        f = float(v.text)
        return int(f) if f == int(f) else f
    except ValueError:
        return v.text


def _ler_letra_simulador(arq: Path) -> InfoCusto | None:
    id_pedido = _extrair_id_pedido(arq.stem)
    if not id_pedido:
        log.warning("    [SKIP] ID nao encontrado em '%s'", arq.name)
        return None
    try:
        with zipfile.ZipFile(arq) as zf:
            shared    = _carregar_shared_strings(zf)
            sheet_xml = zf.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(sheet_xml)
        encontrados: dict[str, object] = {}
        for row in root.iter(f"{{{_NS}}}row"):
            if row.get("r") not in ("5", "12"):
                continue
            for c in row.findall(f"{{{_NS}}}c"):
                ref = c.get("r", "")
                if ref in _CELULAS_ALVO:
                    encontrados[ref] = _resolver_valor(c, shared)
            if len(encontrados) == len(_CELULAS_ALVO):
                break
        letra_com = encontrados.get("Z5")
        status    = encontrados.get("AB12")
        log.debug("    '%s' -> ID %s | letra='%s' | status='%s'", arq.name, id_pedido, letra_com, status)
        return InfoCusto(id_pedido=id_pedido, letra_com=letra_com, status=status)
    except Exception as exc:
        log.exception("    [ERRO] Lendo '%s': %s", arq.name, exc)
        return None


def _ler_simuladores_em_paralelo(arquivos: dict[str, Path]) -> dict[str, InfoCusto | None]:
    resultados: dict[str, InfoCusto | None] = {}
    with ThreadPoolExecutor(max_workers=_THREADS_SIMULADORES) as executor:
        futuros = {
            executor.submit(_ler_letra_simulador, arq): nome
            for nome, arq in arquivos.items()
        }
        for futuro in as_completed(futuros):
            nome = futuros[futuro]
            try:
                resultados[nome] = futuro.result()
            except Exception as exc:
                log.exception("    [ERRO] Leitura paralela de '%s': %s", nome, exc)
                resultados[nome] = None
    return resultados


# ═══ MELHORIA #2 — PASTA CUSTO VAZIA ══════════════════

def _descobrir_simuladores(pasta_base: Path, blacklist: set[str]) -> dict[str, Path]:
    """
    Localiza simuladores na pasta CUSTO de cada vendedor.
    Melhoria #2: cria pasta CUSTO vazia se a pasta do mes existir mas CUSTO nao.
    """
    if not pasta_base.exists():
        log.error("Pasta nao encontrada: %s", pasta_base)
        return {}

    simuladores: dict[str, Path] = {}

    for pasta_vendedor in sorted(pasta_base.iterdir()):
        if not pasta_vendedor.is_dir():
            continue
        if pasta_vendedor.name.upper() in blacklist or pasta_vendedor.name == config.MES_REF:
            log.debug("  [BLACKLIST] Pasta ignorada: %s", pasta_vendedor.name)
            continue

        pasta_mes   = pasta_vendedor / config.MES_REF
        pasta_custo = pasta_mes / config.PASTA_CUSTO

        if not pasta_custo.exists():
            encontradas = [
                s for s in pasta_vendedor.rglob("*")
                if s.is_dir()
                and s.name.upper() == config.PASTA_CUSTO.upper()
                and config.MES_REF.upper() in str(s).upper()
            ]
            if encontradas:
                pasta_custo = encontradas[0]
            else:
                # Melhoria #2
                if pasta_mes.exists():
                    try:
                        pasta_custo.mkdir(parents=True, exist_ok=True)
                        log.info(
                            "  [ESTRUTURA] Pasta CUSTO criada para '%s': %s",
                            pasta_vendedor.name, pasta_custo,
                        )
                    except Exception as exc:
                        log.warning(
                            "  Nao foi possivel criar pasta CUSTO para '%s': %s",
                            pasta_vendedor.name, exc,
                        )
                else:
                    log.debug("  – Sem pasta do mes para '%s'.", pasta_vendedor.name)
                continue

        for arq in sorted(pasta_custo.iterdir()):
            if arq.suffix.lower() in _EXT_SIMULADOR and not arq.name.startswith("~$"):
                simuladores[arq.name] = arq

    log.info("  %d simuladores encontrados em '%s'.", len(simuladores), pasta_base)
    return simuladores


# ═══ MELHORIA RETROATIVA — BUSCA EM MESES ANTERIORES ══

_MAX_MESES_RETROATIVOS = 3   # quantos meses anteriores vasculhar no máximo


def _meses_anteriores_compras(filial: str) -> list[Path]:
    """
    Lista até _MAX_MESES_RETROATIVOS pastas de meses ANTERIORES ao mês atual
    na pasta de compras da filial, em ordem DECRESCENTE (mais recente primeiro).

    Só meses cujo prefixo numérico seja MENOR que o mês atual são incluídos,
    garantindo busca estritamente retroativa (nunca para frente).

    Estrutura esperada:
      PASTA_COMPRADOR / {MM_NOMEMES} / {filial}
    """
    raiz = config.PASTA_COMPRADOR
    if not raiz.exists():
        return []

    # Extrai o número do mês atual a partir do prefixo "MM_" de config.MES_REF
    try:
        mes_atual_num = int(config.MES_REF.split("_")[0])
    except (ValueError, IndexError):
        mes_atual_num = 99  # fallback: aceita tudo

    pastas: list[tuple[str, Path]] = []
    for item in raiz.iterdir():
        if not item.is_dir() or item.name == config.MES_REF:
            continue
        # Aceita apenas meses anteriores (número menor)
        try:
            num = int(item.name.split("_")[0])
        except (ValueError, IndexError):
            continue
        if num >= mes_atual_num:
            continue   # mesmo mês ou futuro — ignorar
        pasta_filial = item / filial
        if pasta_filial.exists():
            pastas.append((num, item.name, pasta_filial))

    # Ordena decrescente pelo número do mês e limita a _MAX_MESES_RETROATIVOS
    pastas.sort(key=lambda t: t[0], reverse=True)
    return [p for _, _, p in pastas[:_MAX_MESES_RETROATIVOS]]


def _buscar_simulador_retroativo(nome_arq: str, filial: str) -> Path | None:
    """
    Procura `nome_arq` retroativamente nas pastas de compras de meses anteriores
    (raiz, OK e ERRO de cada mês), do mais recente para o mais antigo.

    Retorna o Path do primeiro encontrado, ou None.
    """
    for pasta_mes_filial in _meses_anteriores_compras(filial):
        for subpasta in (pasta_mes_filial, pasta_mes_filial / "OK", pasta_mes_filial / "ERRO"):
            candidato = subpasta / nome_arq
            if candidato.exists():
                log.debug(
                    "    [RETROATIVO] Encontrado '%s' em '%s'", nome_arq, subpasta
                )
                return candidato
    return None


def _ids_ja_tratados_comprador(filial: str) -> set[str]:
    """
    Retorna o conjunto de IDs de pedido que já foram tratados pelo comprador
    no mês atual — ou seja, estão em /OK ou /ERRO da pasta do coordenador.

    Esses IDs devem ser IGNORADOS pela busca retroativa:
      - /OK   → já validado, não precisa de nada
      - /ERRO → rejeitado, vendedor deve corrigir e repostar na pasta CUSTO
                  do mês atual; o fluxo normal cuida disso
    """
    pasta_filial, pasta_ok, pasta_erro = _pasta_coordenador_filial(filial)
    ids_tratados: set[str] = set()

    for subpasta in (pasta_ok, pasta_erro):
        if not subpasta.exists():
            continue
        for arq in subpasta.iterdir():
            if not arq.is_file():
                continue
            if arq.suffix.lower() not in _EXT_SIMULADOR or arq.name.startswith("~$"):
                continue
            id_p = _extrair_id_pedido(arq.stem)
            if id_p:
                ids_tratados.add(id_p)
                log.debug(
                    "    [RETROATIVO-SKIP] Pedido %s já em '%s' — ignorado na busca retroativa.",
                    id_p, subpasta.name,
                )

    return ids_tratados


def _buscar_simuladores_retroativos(
    ids_nao_encontrados: set[str],
    filial: str,
    pasta_vendedor: Path,
    blacklist: set[str],
) -> dict[str, Path]:
    """
    Para cada ID de pedido não encontrado na pasta CUSTO do vendedor (mês atual),
    varre os meses ANTERIORES da pasta de compras da filial em busca do simulador.

    Regras de segurança antes de buscar retroativamente:
      - IDs já presentes em /OK  → ignorar (já validado pelo comprador)
      - IDs já presentes em /ERRO → ignorar (vendedor deve corrigir e repostar
        na pasta CUSTO; quando fizer isso, o fluxo normal de cópia para o
        comprador cuida do restante — sem interferência do retroativo)

    Se encontrado nos meses anteriores:
      - Copia para a raiz da pasta do coordenador do mês atual;
      - Retorna {nome_arquivo: caminho_copiado}.
    """
    if not ids_nao_encontrados:
        return {}

    # Exclui IDs que o comprador já tratou (OK ou ERRO) — não tocar neles
    ids_ja_tratados = _ids_ja_tratados_comprador(filial)
    ids_para_buscar = ids_nao_encontrados - ids_ja_tratados

    if not ids_para_buscar:
        log.debug("  [RETROATIVO] Todos os IDs faltantes já foram tratados pelo comprador [%s].", filial)
        return {}

    if ids_ja_tratados & ids_nao_encontrados:
        log.info(
            "  [RETROATIVO] %d pedido(s) ignorado(s) — já em OK/ERRO do coordenador [%s].",
            len(ids_ja_tratados & ids_nao_encontrados), filial,
        )

    pasta_destino_filial, _, _ = _pasta_coordenador_filial(filial)
    recuperados: dict[str, Path] = {}

    for pasta_mes_filial in _meses_anteriores_compras(filial):
        if not ids_para_buscar:
            break   # todos já foram encontrados

        for subpasta in (pasta_mes_filial, pasta_mes_filial / "OK", pasta_mes_filial / "ERRO"):
            if not subpasta.exists():
                continue
            for arq in sorted(subpasta.iterdir()):
                if not arq.is_file():
                    continue
                if arq.suffix.lower() not in _EXT_SIMULADOR or arq.name.startswith("~$"):
                    continue
                id_p = _extrair_id_pedido(arq.stem)
                if not id_p or id_p not in ids_para_buscar:
                    continue

                # Encontrou — copiar para raiz da pasta do coordenador do mês atual
                destino = pasta_destino_filial / arq.name
                if not destino.exists():
                    try:
                        shutil.copy2(arq, destino)
                        log.info(
                            "    [RETROATIVO] Pedido %s — '%s' copiado de '%s' para comprador/%s",
                            id_p, arq.name, subpasta.parent.name, filial,
                        )
                    except Exception as exc:
                        log.warning(
                            "    [RETROATIVO] Erro ao copiar '%s': %s", arq.name, exc
                        )
                        continue
                else:
                    log.debug(
                        "    [RETROATIVO] Pedido %s — '%s' já existe na raiz do coordenador, ignorado.",
                        id_p, arq.name,
                    )

                recuperados[arq.name] = destino
                ids_para_buscar.discard(id_p)

    if recuperados:
        log.info(
            "  [RETROATIVO] %d simulador(es) recuperado(s) de meses anteriores [%s].",
            len(recuperados), filial,
        )
    return recuperados


def _arquivo_ajustado(nome_arq: str) -> bool:
    stem = Path(nome_arq).stem.upper()
    return stem.endswith(" OK") or stem.endswith("_OK")


# ═══ MELHORIA #1 — ESTRUTURA OK/ERRO DO COORDENADOR ══

def _pasta_coordenador_filial(filial: str) -> tuple[Path, Path, Path]:
    """
    Garante existencia de:
      PASTA_COMPRADOR / MES_REF / {filial} /
      PASTA_COMPRADOR / MES_REF / {filial} / OK/
      PASTA_COMPRADOR / MES_REF / {filial} / ERRO/
    Retorna (pasta_filial, pasta_ok, pasta_erro).
    """
    pasta_filial = config.PASTA_COMPRADOR / config.MES_REF / filial
    pasta_ok     = pasta_filial / "OK"
    pasta_erro   = pasta_filial / "ERRO"
    for p in (pasta_filial, pasta_ok, pasta_erro):
        p.mkdir(parents=True, exist_ok=True)
    return pasta_filial, pasta_ok, pasta_erro


# ═══ MELHORIA #3 — LOCALIZACAO E DECISAO POR PASTA ═══

class LocalizacaoSimulador:
    """Localizacao de um simulador nas tres subpastas do coordenador."""

    def __init__(self):
        self.na_raiz: Path | None = None
        self.no_ok:   Path | None = None
        self.no_erro: Path | None = None

    @property
    def situacao(self) -> str:
        """
        Situacao consolidada:
          'no_ok'       — em OK (3b)
          'raiz_e_erro' — raiz + ERRO (3d)
          'na_raiz'     — so na raiz (3a)
          'no_erro'     — so em ERRO (3c)
          'nao_existe'  — nao encontrado
        """
        tem_ok   = self.no_ok   is not None
        tem_raiz = self.na_raiz is not None
        tem_erro = self.no_erro is not None

        if tem_ok:
            return "no_ok"
        if tem_raiz and tem_erro:
            return "raiz_e_erro"
        if tem_raiz:
            return "na_raiz"
        if tem_erro:
            return "no_erro"
        return "nao_existe"


def _localizar_simulador_coordenador(
    nome_arq: str,
    pasta_filial: Path,
    pasta_ok: Path,
    pasta_erro: Path,
) -> LocalizacaoSimulador:
    loc  = LocalizacaoSimulador()
    stem = Path(nome_arq).stem.upper()
    for pasta, attr in (
        (pasta_filial, "na_raiz"),
        (pasta_ok,     "no_ok"),
        (pasta_erro,   "no_erro"),
    ):
        if not pasta.exists():
            continue
        for candidato in pasta.iterdir():
            if (
                candidato.is_file()
                and candidato.suffix.lower() in _EXT_SIMULADOR
                and not candidato.name.startswith("~$")
                and candidato.stem.upper() == stem
            ):
                setattr(loc, attr, candidato)
                break
    return loc


def _arquivos_iguais(arq_a: Path, arq_b: Path) -> bool:
    try:
        if arq_a.stat().st_size != arq_b.stat().st_size:
            return False
        with arq_a.open("rb") as fa, arq_b.open("rb") as fb:
            while True:
                ca = fa.read(65536)
                cb = fb.read(65536)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except Exception:
        return False


def _nome_base(nome_arq: str) -> str:
    """
    Retorna o nome do arquivo sem sufixo OK para comparação.
    'N°887 OK.xlsm' → 'N°887.xlsm'
    'N°887_OK.xlsm' → 'N°887.xlsm'
    'N°887.xlsm'    → 'N°887.xlsm'
    """
    p    = Path(nome_arq)
    stem = p.stem
    for sufixo in (" OK", "_OK"):
        if stem.upper().endswith(sufixo.upper()):
            stem = stem[: -len(sufixo)]
            break
    return stem + p.suffix


def _deve_copiar(origem: Path, loc: LocalizacaoSimulador) -> tuple[bool, str]:
    """
    Regras da Melhoria #3:
      3a na_raiz         -> nao copiar
      3b no_ok           -> nao copiar
      3c no_erro, igual  -> nao copiar
      3c no_erro, difer. -> copiar (vendedor alterou)
      3d raiz_e_erro     -> nao copiar
    """
    sit = loc.situacao
    if sit == "no_ok":
        return False, "ja em OK (validado)"
    if sit == "na_raiz":
        return False, "ja na raiz (aguardando validacao)"
    if sit == "raiz_e_erro":
        return False, "em raiz + ERRO (compras ainda nao validou)"
    if sit == "no_erro":
        if _arquivos_iguais(origem, loc.no_erro):
            return False, "em ERRO e sem alteracao"
        return True, "em ERRO e alterado pelo vendedor — recopia para raiz"
    return True, "novo simulador"


def _copiar_para_coordenador(simuladores_vendor: dict[str, Path], filial: str) -> None:
    """Copia simuladores do vendedor para a raiz da filial do coordenador (Melhorias #1 e #3).
    Correção: arquivos sem ID de pedido identificável no nome são ignorados."""
    pasta_filial, pasta_ok, pasta_erro = _pasta_coordenador_filial(filial)
    copiados = ignorados = sem_id = 0
    for nome, origem in simuladores_vendor.items():
        if not _extrair_id_pedido(Path(_nome_base(nome)).stem):
            log.debug("    [SEM-ID] Ignorado (sem numero de pedido no nome): %s", nome)
            sem_id += 1
            continue
        loc   = _localizar_simulador_coordenador(nome, pasta_filial, pasta_ok, pasta_erro)
        deve, motivo = _deve_copiar(origem, loc)
        if not deve:
            log.debug("    Ignorado (%s): %s", motivo, nome)
            ignorados += 1
        else:
            arq_destino = pasta_filial / nome
            try:
                shutil.copy2(origem, arq_destino)
                log.debug("    Copiado (%s): %s", motivo, nome)
                copiados += 1
            except Exception as exc:
                log.error("    Erro ao copiar '%s': %s", nome, exc)
    log.info(
        "  Simuladores [%s] -> coordenador: %d copiados | %d ignorados | %d sem ID.",
        filial, copiados, ignorados, sem_id,
    )




def _copiar_para_analista(simuladores_vendor: dict[str, Path], filial: str) -> None:
    """
    Copia simuladores do vendedor para a pasta da analista de vendas.

    Regras simples (sem lógica de OK/ERRO):
      - Destino: PASTA_ANALISTA_SIMULADORES / MES_REF / {filial} /
      - Se o arquivo já existir no destino, não sobrescreve.
      - Só executa se config.RELATORIO_ANALISTA_ATIVO == True.
    """
    if not config.RELATORIO_ANALISTA_ATIVO:
        return

    destino = config.PASTA_ANALISTA_SIMULADORES / config.MES_REF / filial
    destino.mkdir(parents=True, exist_ok=True)

    copiados = ignorados = sem_id = 0
    for nome, origem in simuladores_vendor.items():
        if not _extrair_id_pedido(Path(_nome_base(nome)).stem):
            log.debug("    [ANALISTA-SEM-ID] Ignorado (sem numero de pedido no nome): %s", nome)
            sem_id += 1
            continue
        arq_destino = destino / nome
        if arq_destino.exists():
            log.debug("    [ANALISTA] Ignorado (já existe): %s", nome)
            ignorados += 1
        else:
            try:
                shutil.copy2(origem, arq_destino)
                log.debug("    [ANALISTA] Copiado: %s", nome)
                copiados += 1
            except Exception as exc:
                log.error("    [ANALISTA] Erro ao copiar '%s': %s", nome, exc)

    log.info(
        "  Simuladores [%s] -> analista: %d copiados | %d já existiam | %d sem ID.",
        filial, copiados, ignorados, sem_id,
    )

# ═══ DESCOBERTA — PASTA DO COORDENADOR ════════════════

def _descobrir_simuladores_comprador_filial(filial: str) -> dict[str, Path]:
    """
    Busca validados em:
      1. Subpasta OK (nova estrutura — Melhoria #1)
      2. Raiz com sufixo OK no nome (compatibilidade legada)
    """
    pasta_filial, pasta_ok, _ = _pasta_coordenador_filial(filial)
    simuladores: dict[str, Path] = {}
    total = validados = 0

    for arq in sorted(pasta_ok.iterdir()):
        if arq.suffix.lower() not in _EXT_SIMULADOR or arq.name.startswith("~$"):
            continue
        total += 1
        simuladores[arq.name] = arq
        validados += 1

    for arq in sorted(pasta_filial.iterdir()):
        if not arq.is_file():
            continue
        if arq.suffix.lower() not in _EXT_SIMULADOR or arq.name.startswith("~$"):
            continue
        total += 1
        if _arquivo_ajustado(arq.name):
            simuladores[arq.name] = arq
            validados += 1

    log.info(
        "  Coordenador [%s]: %d encontrados, %d validados.",
        filial, total, validados,
    )
    return simuladores


def _descobrir_simuladores_comprador() -> dict[str, Path]:
    return {**_descobrir_simuladores_comprador_filial("SP"),
            **_descobrir_simuladores_comprador_filial("MG")}


def _nome_base(nome_arq: str) -> str:
    p    = Path(nome_arq)
    stem = p.stem
    for sufixo in (" OK", "_OK"):
        if stem.upper().endswith(sufixo.upper()):
            stem = stem[: -len(sufixo)]
            break
    return stem + p.suffix


# ═══ MELHORIA #4/#5 — MAPEAMENTO DE SITUACAO POR PEDIDO

def _mapear_situacao_simuladores(filial: str) -> dict[str, LocalizacaoSimulador]:
    """
    Retorna {id_pedido_zfill6: LocalizacaoSimulador} varrendo as tres subpastas.
    Usado para definir obs (Melhoria #4) e flag de cor vermelha (Melhoria #5).
    """
    pasta_filial, pasta_ok, pasta_erro = _pasta_coordenador_filial(filial)
    mapa: dict[str, LocalizacaoSimulador] = {}

    def _indexar(pasta: Path, attr: str) -> None:
        if not pasta.exists():
            return
        for arq in pasta.iterdir():
            if not arq.is_file():
                continue
            if arq.suffix.lower() not in _EXT_SIMULADOR or arq.name.startswith("~$"):
                continue
            id_p = _extrair_id_pedido(arq.stem)
            if not id_p:
                continue
            if id_p not in mapa:
                mapa[id_p] = LocalizacaoSimulador()
            setattr(mapa[id_p], attr, arq)

    _indexar(pasta_filial, "na_raiz")
    _indexar(pasta_ok,     "no_ok")
    _indexar(pasta_erro,   "no_erro")
    return mapa


# ═══ CALCULO DE COMISSOES ══════════════════════════════

def calcular_comissoes(pedidos: list[Pedido]) -> list[Pedido]:
    log.info("═══ Calculando comissoes ═══")
    blacklist = _carregar_blacklist()

    # Carrega e aplica comissões fixas antes do loop de simuladores.
    # Pedidos com comissao_fixa=True já têm comissao_compras_pct definida;
    # o loop abaixo vai usar esse valor ao comparar com o simulador do vendedor.
    fixas = carregar_comissoes_fixas()
    _aplicar_comissoes_fixas(pedidos, fixas)

    idx: dict[str, list[Pedido]] = {}
    for p in pedidos:
        chave = p.numero_pedido.strip().zfill(6)
        idx.setdefault(chave, []).append(p)

    sims_sp = _descobrir_simuladores(config.PASTA_VENDEDOR_SP, blacklist)
    sims_mg = _descobrir_simuladores(config.PASTA_VENDEDOR_MG, blacklist)
    sims_vendor = {**sims_sp, **sims_mg}
    log.info(
        "  Simuladores: %d SP + %d MG = %d total.",
        len(sims_sp), len(sims_mg), len(sims_vendor),
    )

    # Melhoria #3: copia por regra de pasta
    _copiar_para_coordenador(sims_sp, "SP")
    _copiar_para_coordenador(sims_mg, "MG")

    # Copia para pasta da analista (simples, sem lógica de OK/ERRO)
    _copiar_para_analista(sims_sp, "SP")
    _copiar_para_analista(sims_mg, "MG")

    # ── Busca retroativa (melhoria nova) ─────────────────────────────────────
    # Identifica IDs presentes em pedidos mas ausentes nos simuladores dos vendedores
    ids_com_simulador = {
        id_p
        for nome in sims_vendor
        for id_p in [_extrair_id_pedido(Path(_nome_base(nome)).stem)]
        if id_p
    }
    ids_todos_pedidos = set(idx.keys())
    ids_faltando_sp   = ids_todos_pedidos - ids_com_simulador
    ids_faltando_mg   = ids_todos_pedidos - ids_com_simulador

    # Busca retroativa: SP
    sims_retro_sp = _buscar_simuladores_retroativos(
        ids_nao_encontrados=ids_faltando_sp,
        filial="SP",
        pasta_vendedor=config.PASTA_VENDEDOR_SP,
        blacklist=blacklist,
    )
    # Busca retroativa: MG (sobre os que ainda faltam após SP)
    sims_retro_mg = _buscar_simuladores_retroativos(
        ids_nao_encontrados=ids_faltando_mg - {
            id_p
            for nome in sims_retro_sp
            for id_p in [_extrair_id_pedido(Path(_nome_base(nome)).stem)]
            if id_p
        },
        filial="MG",
        pasta_vendedor=config.PASTA_VENDEDOR_MG,
        blacklist=blacklist,
    )

    # Incorpora retroativos ao conjunto de simuladores do vendedor para leitura
    sims_vendor = {**sims_vendor, **sims_retro_sp, **sims_retro_mg}
    if sims_retro_sp or sims_retro_mg:
        log.info(
            "  Apos retroativo: %d simuladores totais (%d recuperados SP + %d MG).",
            len(sims_vendor), len(sims_retro_sp), len(sims_retro_mg),
        )
    # ─────────────────────────────────────────────────────────────────────────

    # Melhoria #4/#5: mapeamento de situacao
    situacao_coord: dict[str, LocalizacaoSimulador] = {
        **_mapear_situacao_simuladores("SP"),
        **_mapear_situacao_simuladores("MG"),
    }

    sims_comprador = _descobrir_simuladores_comprador()
    idx_comprador: dict[str, Path] = {
        _nome_base(nome): arq for nome, arq in sims_comprador.items()
    }

    sims_vendor_para_ler = {
        nome: arq for nome, arq in sims_vendor.items()
        if _extrair_id_pedido(Path(nome).stem)
    }
    log.info(
        "  Lendo em paralelo: %d vendor + %d coordenador…",
        len(sims_vendor_para_ler), len(sims_comprador),
    )
    infos_vendor    = _ler_simuladores_em_paralelo(sims_vendor_para_ler)
    infos_comprador = _ler_simuladores_em_paralelo(sims_comprador)

    total_ok = total_pendente = total_skip = total_erro = 0
    ids_erro: set[str] = set()  # pedidos com simulador exclusivamente em ERRO

    # IDs já processados — evita dupla contagem entre o loop principal e o retroativo
    ids_processados: set[str] = set()

    # ── Loop principal: simuladores presentes na pasta CUSTO do vendedor ──────
    for nome_arq, arq_vendor in sims_vendor.items():
        try:
            id_pedido = _extrair_id_pedido(Path(nome_arq).stem)
            if not id_pedido:
                log.warning("    [SKIP] ID nao encontrado em '%s'", nome_arq)
                total_skip += 1
                continue

            linhas_pedido = idx.get(id_pedido)
            if not linhas_pedido:
                log.debug("    [SKIP] Pedido %s nao encontrado.", id_pedido)
                total_skip += 1
                continue

            info_vendor = infos_vendor.get(nome_arq)
            if info_vendor is None:
                total_skip += 1
                continue

            letra_v = str(info_vendor.letra_com or "").strip().upper()
            pct_v   = 0.0 if _is_prejuizo(info_vendor.status) else config.TABELA_COMISSAO.get(letra_v, 0.0)

            nome_comprador    = idx_comprador.get(_nome_base(nome_arq))
            info_comprador    = infos_comprador.get(nome_comprador.name) if nome_comprador else None
            comprador_ajustou = info_comprador is not None

            loc_coord = situacao_coord.get(id_pedido)
            sit = loc_coord.situacao if loc_coord else "nao_existe"

            # Verifica se alguma linha deste pedido tem comissão fixa
            tem_fixa      = any(getattr(l, "comissao_fixa", False) for l in linhas_pedido)
            pct_fixa      = linhas_pedido[0].comissao_compras_pct if tem_fixa else 0.0

            if comprador_ajustou:
                letra_c   = str(info_comprador.letra_com or "").strip().upper()
                pct_c     = 0.0 if _is_prejuizo(info_comprador.status) else config.TABELA_COMISSAO.get(letra_c, 0.0)
                pct_menor = min(pct_v, pct_c)
                prejuizo  = pct_menor == 0.0 and (
                    _is_prejuizo(info_vendor.status) or _is_prejuizo(info_comprador.status)
                )
                # Comissão fixa é teto absoluto: mesmo com simulador validado pelo
                # comprador, o pedido nunca paga mais do que o valor fixado para
                # aquele cliente. O simulador pode estar em OK normalmente, mas o
                # percentual considerado é limitado ao teto.
                if tem_fixa and pct_menor > pct_fixa:
                    pct_menor = pct_fixa
                obs = "Comissao Definida! - Prejuizo" if prejuizo else "Comissao Definida!"
            elif tem_fixa:
                # Comprador ainda não avaliou, mas comissão já está definida pela
                # planilha de fixas — compara com o simulador do vendedor e usa a menor.
                pct_c     = pct_fixa
                pct_menor = min(pct_v, pct_c)
                prejuizo  = pct_menor == 0.0 and _is_prejuizo(info_vendor.status)
                obs = "Comissao Definida! - Prejuizo" if prejuizo else "Comissao Definida!"
            else:
                pct_c     = 0.0
                pct_menor = 0.0
                if sit == "no_erro":
                    obs = "Ajuste a planilha de custo"
                    ids_erro.add(id_pedido)
                else:
                    obs = "Analise de Compras pendente!"

            em_erro = (sit == "no_erro")

            for linha in linhas_pedido:
                linha.comissao_vendedor_pct = pct_v
                linha.comissao_compras_pct  = pct_c
                linha.comissao_menor_pct    = pct_menor
                linha.valor_comissao_menor  = round(linha.valor_faturado * pct_menor, 2)
                linha.obs_comissao          = obs
                linha.em_erro               = em_erro

            ids_processados.add(id_pedido)
            n = len(linhas_pedido)
            if comprador_ajustou:
                log.info("    [OK] Pedido %s (%d linha(s)) -> %s", id_pedido, n, obs)
                total_ok += 1
            else:
                log.info("    [PENDENTE] Pedido %s (%d linha(s)) sit=%s", id_pedido, n, sit)
                total_pendente += 1

        except Exception as exc:
            log.exception("    [ERRO] '%s': %s", nome_arq, exc)
            total_erro += 1

    # ── Loop retroativo: IDs bloqueados pela _ids_ja_tratados_comprador ───────
    # Pedidos cujo simulador foi copiado retroativamente em execução anterior e
    # o comprador já moveu para /OK ou /ERRO — não estão em sims_vendor mas
    # precisam ser processados para preencher comissão e obs corretamente.
    for filial in ("SP", "MG"):
        _, pasta_ok, pasta_erro = _pasta_coordenador_filial(filial)

        # /OK: simulador validado pelo comprador — lê comissão diretamente do arquivo OK
        for arq_ok in sorted(pasta_ok.iterdir()) if pasta_ok.exists() else []:
            if not arq_ok.is_file():
                continue
            if arq_ok.suffix.lower() not in _EXT_SIMULADOR or arq_ok.name.startswith("~$"):
                continue
            id_pedido = _extrair_id_pedido(arq_ok.stem)
            if not id_pedido or id_pedido in ids_processados:
                continue
            linhas_pedido = idx.get(id_pedido)
            if not linhas_pedido:
                continue
            try:
                info_ok = _ler_letra_simulador(arq_ok)
                if info_ok is None:
                    continue
                letra_c   = str(info_ok.letra_com or "").strip().upper()
                pct_c     = 0.0 if _is_prejuizo(info_ok.status) else config.TABELA_COMISSAO.get(letra_c, 0.0)
                # Sem simulador do vendedor disponivel: usa pct_c como pct_v tambem
                pct_menor = pct_c
                prejuizo  = pct_menor == 0.0 and _is_prejuizo(info_ok.status)
                obs       = "Comissao Definida! - Prejuizo" if prejuizo else "Comissao Definida!"
                for linha in linhas_pedido:
                    linha.comissao_vendedor_pct = pct_c   # melhor aproximacao disponivel
                    linha.comissao_compras_pct  = pct_c
                    linha.comissao_menor_pct    = pct_menor
                    linha.valor_comissao_menor  = round(linha.valor_faturado * pct_menor, 2)
                    linha.obs_comissao          = obs
                    linha.em_erro               = False
                ids_processados.add(id_pedido)
                log.info(
                    "    [OK-RETROATIVO] Pedido %s (%d linha(s)) -> %s",
                    id_pedido, len(linhas_pedido), obs,
                )
                total_ok += 1
            except Exception as exc:
                log.exception("    [ERRO-RETROATIVO-OK] Pedido %s: %s", id_pedido, exc)
                total_erro += 1

        # /ERRO: simulador rejeitado — obs vermelha, sem comissao
        for arq_erro in sorted(pasta_erro.iterdir()) if pasta_erro.exists() else []:
            if not arq_erro.is_file():
                continue
            if arq_erro.suffix.lower() not in _EXT_SIMULADOR or arq_erro.name.startswith("~$"):
                continue
            id_pedido = _extrair_id_pedido(arq_erro.stem)
            if not id_pedido or id_pedido in ids_processados:
                continue
            linhas_pedido = idx.get(id_pedido)
            if not linhas_pedido:
                continue
            obs = "Ajuste a planilha de custo"
            ids_erro.add(id_pedido)
            for linha in linhas_pedido:
                linha.comissao_vendedor_pct = 0.0
                linha.comissao_compras_pct  = 0.0
                linha.comissao_menor_pct    = 0.0
                linha.valor_comissao_menor  = 0.0
                linha.obs_comissao          = obs
                linha.em_erro               = True
            ids_processados.add(id_pedido)
            log.info(
                "    [ERRO-RETROATIVO] Pedido %s (%d linha(s)) -> linha vermelha",
                id_pedido, len(linhas_pedido),
            )
            total_pendente += 1

    log.info(
        "  Resumo: %d OK | %d pendentes | %d ignorados | %d erros",
        total_ok, total_pendente, total_skip, total_erro,
    )
    database.atualizar_comissoes(pedidos, config.ANO_MES_REF)
    return pedidos, ids_erro


# Comissão aplicada automaticamente a pedidos faturados sem simulador (fabricação interna)
_PCT_FABRICACAO_INTERNA = 0.02   # 2%


def marcar_sem_simulador(pedidos: list[Pedido]) -> None:
    """
    Classifica pedidos que ainda não têm obs definida após calcular_comissoes:

    - Sem NF → "Pedido ainda nao faturado" (sem comissão)
    - Com NF, sem simulador:
        * Cliente com comissão fixa → usa o pct da planilha comissoes_fixas como
          estimativa (comissao_compras_pct já preenchida por _aplicar_comissoes_fixas).
          Quando o vendedor adicionar o simulador, calcular_comissoes comparará e
          escolherá o menor.
        * Demais clientes → 2% automático ("Fabricacao interna / simulador ausente")
    """
    sem_nf = fab_interna = fab_fixa = 0
    for p in pedidos:
        if p.obs_comissao.strip():
            continue   # já classificado por calcular_comissoes

        tem_nf = p.nota_fiscal not in ("-", "", None)

        if not tem_nf:
            p.obs_comissao = "Pedido ainda nao faturado"
            sem_nf += 1
        elif p.comissao_fixa:
            # Estimativa com comissão fixa — compras já definida, aguarda simulador
            pct = p.comissao_compras_pct
            p.comissao_menor_pct   = pct
            p.valor_comissao_menor = round(p.valor_faturado * pct, 2)
            p.obs_comissao         = "Fabricacao interna / simulador ausente"
            fab_fixa += 1
        else:
            # Sem comissão fixa: aplica 2% como estimativa padrão
            p.comissao_menor_pct   = _PCT_FABRICACAO_INTERNA
            p.valor_comissao_menor = round(p.valor_faturado * _PCT_FABRICACAO_INTERNA, 2)
            p.obs_comissao         = "Fabricacao interna / simulador ausente"
            fab_interna += 1

    if sem_nf:
        log.info("  %d pedido(s) ainda nao faturado(s).", sem_nf)
    if fab_fixa:
        log.info("  %d pedido(s) com comissao fixa (aguardando simulador do vendedor).", fab_fixa)
    if fab_interna:
        log.info("  %d pedido(s) sem simulador (comissao 2%% aplicada).", fab_interna)
    if not sem_nf and not fab_fixa and not fab_interna:
        log.info("  Todos os pedidos com obs definida.")


# ═══ CONVERSAO PARA DATAFRAME ══════════════════════════

def pedidos_para_df(pedidos: list[Pedido]) -> pd.DataFrame:
    return pd.DataFrame([p.to_dict() for p in pedidos])

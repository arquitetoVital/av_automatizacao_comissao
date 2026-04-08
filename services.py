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

import config
import database
from clients import OmieClient
from models import InfoCusto, Pedido
from utils import nome_para_pasta

log = logging.getLogger(__name__)

_THREADS_SIMULADORES = 8
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
    blacklist = _carregar_blacklist()

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
        "  OMIE: %d com NF | %d sem NF | %d blacklistados | %d NFs sem pedido.",
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

def _meses_anteriores_compras(filial: str) -> list[Path]:
    """
    Lista as pastas de meses anteriores na pasta de compras da filial,
    em ordem decrescente (mais recente primeiro).

    Estrutura esperada:
      PASTA_COMPRADOR / {MM_MES} / {filial}

    Retorna somente pastas que existem e sejam diferentes do mês atual.
    """
    raiz = config.PASTA_COMPRADOR
    if not raiz.exists():
        return []

    pastas: list[tuple[str, Path]] = []
    for item in raiz.iterdir():
        if not item.is_dir():
            continue
        if item.name == config.MES_REF:
            continue   # mês atual — ignorar
        pasta_filial = item / filial
        if pasta_filial.exists():
            pastas.append((item.name, pasta_filial))

    # Ordena decrescente pelo nome da pasta (ex: "03_MARÇO" > "02_FEVEREIRO")
    pastas.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in pastas]


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


def _buscar_simuladores_retroativos(
    ids_nao_encontrados: set[str],
    filial: str,
    pasta_vendedor: Path,
    blacklist: set[str],
) -> dict[str, Path]:
    """
    Para cada ID de pedido não encontrado na pasta do vendedor,
    varre os meses anteriores da pasta de compras (filial) em busca do simulador.

    Se encontrado:
      - copia para a pasta de compras do mês atual (raiz da filial), se ainda não existir;
      - retorna {nome_arquivo: caminho_copiado}.

    Lógica (melhoria 1b):
      Busca de forma retroativa mês a mês até encontrar ou esgotar as opções.
    """
    if not ids_nao_encontrados:
        return {}

    pasta_destino_filial, _, _ = _pasta_coordenador_filial(filial)
    recuperados: dict[str, Path] = {}

    # Monta um índice reverso: id_pedido → lista de nomes de arquivo possíveis
    # Para cada mês anterior, varre todas as subpastas procurando por ID
    for pasta_mes_filial in _meses_anteriores_compras(filial):
        if not ids_nao_encontrados:
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
                if not id_p or id_p not in ids_nao_encontrados:
                    continue

                # Encontrou — copiar para pasta de compras do mês atual
                destino = pasta_destino_filial / arq.name
                if not destino.exists():
                    try:
                        shutil.copy2(arq, destino)
                        log.info(
                            "    [RETROATIVO] Pedido %s — '%s' copiado de '%s' para '%s'",
                            id_p, arq.name, subpasta, pasta_destino_filial,
                        )
                    except Exception as exc:
                        log.warning(
                            "    [RETROATIVO] Erro ao copiar '%s': %s", arq.name, exc
                        )
                        continue
                else:
                    log.debug(
                        "    [RETROATIVO] Pedido %s — '%s' já existe no destino, ignorado.",
                        id_p, arq.name,
                    )

                recuperados[arq.name] = destino
                ids_nao_encontrados.discard(id_p)

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
    """Copia simuladores do vendedor para a raiz da filial do coordenador (Melhorias #1 e #3)."""
    pasta_filial, pasta_ok, pasta_erro = _pasta_coordenador_filial(filial)
    copiados = ignorados = 0
    for nome, origem in simuladores_vendor.items():
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
        "  Simuladores [%s] -> coordenador: %d copiados | %d ignorados.",
        filial, copiados, ignorados,
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

            if comprador_ajustou:
                letra_c   = str(info_comprador.letra_com or "").strip().upper()
                pct_c     = 0.0 if _is_prejuizo(info_comprador.status) else config.TABELA_COMISSAO.get(letra_c, 0.0)
                pct_menor = min(pct_v, pct_c)
                prejuizo  = pct_menor == 0.0 and (
                    _is_prejuizo(info_vendor.status) or _is_prejuizo(info_comprador.status)
                )
                obs = "Comissao Definida! - Prejuizo" if prejuizo else "Comissao Definida!"
            else:
                pct_c     = 0.0
                pct_menor = 0.0
                # Melhoria #4c e #4d
                if sit == "no_erro":
                    obs = "Ajuste a planilha de custo"
                    ids_erro.add(id_pedido)
                else:
                    obs = "Analise de Compras pendente!"

            # Melhoria #5: linha vermelha so quando esta EXCLUSIVAMENTE em ERRO
            em_erro = (sit == "no_erro")

            for linha in linhas_pedido:
                linha.comissao_vendedor_pct = pct_v
                linha.comissao_compras_pct  = pct_c
                linha.comissao_menor_pct    = pct_menor
                linha.valor_comissao_menor  = round(linha.valor_faturado * pct_menor, 2)
                linha.obs_comissao          = obs
                linha.em_erro               = em_erro

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

    log.info(
        "  Resumo: %d OK | %d pendentes | %d ignorados | %d erros",
        total_ok, total_pendente, total_skip, total_erro,
    )
    database.atualizar_comissoes(pedidos, config.ANO_MES_REF)
    return pedidos, ids_erro


def marcar_sem_simulador(pedidos: list[Pedido]) -> None:
    """
    Melhoria #4a: sem NF          -> 'Pedido ainda nao faturado'
    Melhoria #4b: com NF, sem obs -> 'Adicione o simulador na pasta CUSTO'
    """
    sem_nf = sem_simulador = 0
    for p in pedidos:
        tem_nf  = p.nota_fiscal not in ("-", "", None)
        sem_obs = not p.obs_comissao.strip()
        if not tem_nf and sem_obs:
            p.obs_comissao = "Pedido ainda nao faturado"
            sem_nf += 1
        elif tem_nf and sem_obs:
            p.obs_comissao = "Adicione o simulador na pasta CUSTO"
            sem_simulador += 1

    if sem_nf:
        log.info("  %d pedido(s) ainda nao faturado(s).", sem_nf)
    if sem_simulador:
        log.info("  %d pedido(s) faturado(s) sem simulador.", sem_simulador)
    if not sem_nf and not sem_simulador:
        log.info("  Todos os pedidos com obs definida.")


# ═══ CONVERSAO PARA DATAFRAME ══════════════════════════

def pedidos_para_df(pedidos: list[Pedido]) -> pd.DataFrame:
    return pd.DataFrame([p.to_dict() for p in pedidos])

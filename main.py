"""
main.py
───────
Ponto de entrada. Orquestra os passos em ordem, sem lógica de negócio.

Fluxo:
  1. Extração      → OMIE: pedidos com NF + pedidos sem NF incluídos no mês (services)
  2. Comissões     → simuladores custo (services)
  3. Relatório     → coordenador       (reports)
  4. Marcação      → sem simulador     (services)
  5. Distribuição  → vendedores        (reports)
  6. Publicação    → JSON → GitHub     (exporter + github_publisher)
"""

import logging
import sys
from datetime import datetime

import config
import database
import exporter
import github_publisher
import reports
import services

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def main() -> None:
    inicio = datetime.now()
    log.info("════════════════════════════════════════════════")
    log.info("  GERADOR DE COMISSÕES  –  %s", inicio.strftime("%d/%m/%Y %H:%M:%S"))
    log.info("  Mês de referência : %s  (%s)", config.MES_REF, config.ANO_MES_REF)
    log.info("  OMIE período      : %s → %s", config.MES_INICIO_OMIE, config.MES_FIM_OMIE)
    log.info("════════════════════════════════════════════════")

    # ── Banco de dados ───────────────────────────────────
    database.inicializar()
    database.limpar_mes_anterior(config.ANO_MES_REF)
    database.limpar_pedidos_sem_vendedor(config.ANO_MES_REF)

    # ── Passo 1: Extração OMIE ───────────────────────────
    log.info("══ PASSO 1: extração OMIE ══")
    pedidos = services.extrair_omie()
    log.info("  Total: %d pedidos", len(pedidos))

    # ── Passo 2: Comissões ───────────────────────────────
    log.info("══ PASSO 2: comissões ══")
    try:
        pedidos, ids_erro = services.calcular_comissoes(pedidos)
    except Exception as exc:
        log.critical(
            "Falha crítica no cálculo de comissões: %s — abortando execução.", exc,
            exc_info=True,
        )
        sys.exit(1)

    # Converte para DataFrame uma única vez — compartilhado pelos passos 3 e 5
    df = services.pedidos_para_df(pedidos)

    # ── Passo 3: Relatório coordenador ───────────────────
    reports.gerar_relatorio_coordenador(df)

    # ── Passo 4: Marcar sem simulador ────────────────────
    services.marcar_sem_simulador(pedidos)

    # Reconverte após marcação para incluir obs atualizadas no relatório de vendedores
    df = services.pedidos_para_df(pedidos)

    # ── Passo 5: Distribuição por vendedor ───────────────
    log.info("══ PASSO 5: distribuição vendedores ══")
    reports.distribuir_para_vendedores(df)

    # ── Passo 6: Publicação JSON → GitHub ────────────────
    log.info("══ PASSO 6: publicação dashboard ══")
    payload = exporter.gerar_json(df)
    exporter.salvar_json_local(payload)   # salva cópia local como fallback
    github_publisher.publicar(payload)    # commit no repositório privado

    duracao = (datetime.now() - inicio).total_seconds()
    log.info("════════════════════════════════════════════════")
    log.info("  ✅ Concluído em %.1fs", duracao)
    log.info("  📄 Log: %s", config.LOG_FILE.resolve())
    log.info("════════════════════════════════════════════════")


if __name__ == "__main__":
    main()

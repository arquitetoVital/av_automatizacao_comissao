"""
database.py
───────────
Camada de persistência SQLite para o sistema de comissões.

Responsabilidades:
  - Armazenar pedidos do mês evitando reprocessamento
  - Cache de vendedores com TTL de 24h
  - Cache de empresas (clientes) com TTL de 7 dias — mudam raramente
  - Detectar mudanças (valor, status) e atualizar apenas o necessário

Tabelas:
  pedidos          → um registro por pedido do mês (numero_pedido é chave)
  cache_vendedores → {codigo, nome, atualizado_em}  TTL 24h
  cache_empresas   → {codigo, nome, atualizado_em}  TTL 7 dias
  sync_log         → controle de quando cada origem foi sincronizada

O banco fica em comissoes.db na mesma pasta do projeto.
Para resetar completamente: apagar o arquivo .db.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from models import Pedido

log = logging.getLogger(__name__)

DB_PATH          = Path(__file__).parent / "comissoes.db"
TTL_VENDEDORES_H = 24      # horas — vendedores mudam ocasionalmente
TTL_EMPRESAS_D   = 7       # dias  — empresas mudam raramente


# ═══════════════════════════════════════════════════════
#  CONEXÃO E SCHEMA
# ═══════════════════════════════════════════════════════

def _conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # leituras não bloqueiam escritas
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def inicializar() -> None:
    """Cria as tabelas se não existirem e aplica migrations necessárias."""
    log.info("  DB → inicializando %s", DB_PATH)
    with _conectar() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pedidos (
                numero_pedido         TEXT    NOT NULL,
                ano_mes               TEXT    NOT NULL,
                data_venda            TEXT,
                nome_cliente          TEXT,
                nome_vendedor         TEXT,
                valor_pedido          REAL,
                data_nota_fiscal      TEXT,
                nota_fiscal           TEXT,
                valor_faturado        REAL,
                valor_pendente        REAL,
                comissao_vendedor_pct REAL    DEFAULT 0,
                comissao_compras_pct  REAL    DEFAULT 0,
                comissao_menor_pct    REAL    DEFAULT 0,
                valor_comissao_menor  REAL    DEFAULT 0,
                obs_comissao          TEXT    DEFAULT '',
                atualizado_em         TEXT    NOT NULL,
                PRIMARY KEY (numero_pedido, ano_mes)
            );

            CREATE TABLE IF NOT EXISTS cache_vendedores (
                codigo        INTEGER PRIMARY KEY,
                nome          TEXT    NOT NULL DEFAULT '',
                atualizado_em TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cache_empresas (
                codigo        INTEGER PRIMARY KEY,
                nome          TEXT    NOT NULL DEFAULT '',
                atualizado_em TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                origem        TEXT    PRIMARY KEY,
                ultimo_sync   TEXT    NOT NULL
            );
        """)

        # Migrations: remove colunas obsoletas de bancos antigos (SQLite 3.35+)
        for col in ("valor_comissao_vendedor", "valor_comissao_compras"):
            try:
                conn.execute(f"ALTER TABLE pedidos DROP COLUMN {col}")
                log.info("  DB → coluna obsoleta removida: %s", col)
            except Exception:
                pass  # coluna já não existe — ok

    log.info("  DB → schema OK.")


# ═══════════════════════════════════════════════════════
#  PEDIDOS
# ═══════════════════════════════════════════════════════

def _fingerprint(p: Pedido) -> str:
    return f"{p.valor_pedido}|{p.valor_faturado}|{p.nota_fiscal}"


def upsert_pedidos(pedidos: list[Pedido], ano_mes: str) -> tuple[int, int, int]:
    """
    Insere ou atualiza pedidos usando INSERT OR REPLACE do SQLite.
    Preserva os dados de comissão já calculados ao atualizar apenas campos
    de extração OMIE (evita sobrescrever comissões calculadas no passo 2).
    """
    inseridos = atualizados = inalterados = 0
    agora = datetime.now().isoformat(timespec="seconds")

    with _conectar() as conn:
        for p in pedidos:
            row = conn.execute(
                "SELECT valor_pedido, valor_faturado, nota_fiscal "
                "FROM pedidos WHERE numero_pedido=? AND ano_mes=?",
                (p.numero_pedido, ano_mes),
            ).fetchone()

            if row is None:
                conn.execute("""
                    INSERT INTO pedidos VALUES (
                        :numero_pedido, :ano_mes,
                        :data_venda, :nome_cliente, :nome_vendedor,
                        :valor_pedido, :data_nota_fiscal, :nota_fiscal,
                        :valor_faturado, :valor_pendente,
                        0, 0, 0, 0, '', :atualizado_em
                    )
                """, {**_pedido_para_row(p, ano_mes), "atualizado_em": agora})
                inseridos += 1

            elif _fingerprint(p) != f"{row[0]}|{row[1]}|{row[2]}":
                conn.execute("""
                    UPDATE pedidos SET
                        data_venda=:data_venda, nome_cliente=:nome_cliente,
                        nome_vendedor=:nome_vendedor, valor_pedido=:valor_pedido,
                        data_nota_fiscal=:data_nota_fiscal, nota_fiscal=:nota_fiscal,
                        valor_faturado=:valor_faturado, valor_pendente=:valor_pendente,
                        atualizado_em=:atualizado_em
                    WHERE numero_pedido=:numero_pedido AND ano_mes=:ano_mes
                """, {**_pedido_para_row(p, ano_mes), "atualizado_em": agora})
                atualizados += 1

            else:
                inalterados += 1

    log.info(
        "  DB pedidos → %d inseridos | %d atualizados | %d inalterados",
        inseridos, atualizados, inalterados,
    )
    return inseridos, atualizados, inalterados


def carregar_pedidos(ano_mes: str) -> list[Pedido]:
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT * FROM pedidos WHERE ano_mes=? ORDER BY numero_pedido",
            (ano_mes,),
        ).fetchall()
    pedidos = [_row_para_pedido(r) for r in rows]
    log.info("  DB → %d pedidos carregados (%s).", len(pedidos), ano_mes)
    return pedidos


def atualizar_comissoes(pedidos: list[Pedido], ano_mes: str) -> None:
    agora = datetime.now().isoformat(timespec="seconds")
    with _conectar() as conn:
        conn.executemany("""
            UPDATE pedidos SET
                comissao_vendedor_pct=:cv_pct,
                comissao_compras_pct=:cc_pct,
                comissao_menor_pct=:cm_pct,
                valor_comissao_menor=:cm_val,
                obs_comissao=:obs,
                atualizado_em=:agora
            WHERE numero_pedido=:num AND ano_mes=:ano_mes
        """, [
            {
                "cv_pct":  p.comissao_vendedor_pct,
                "cc_pct":  p.comissao_compras_pct,
                "cm_pct":  p.comissao_menor_pct,
                "cm_val":  p.valor_comissao_menor,
                "obs":     p.obs_comissao,
                "agora":   agora,
                "num":     p.numero_pedido,
                "ano_mes": ano_mes,
            }
            for p in pedidos
        ])
    log.info("  DB → comissões persistidas (%d pedidos).", len(pedidos))


def limpar_pedidos_sem_vendedor(ano_mes: str) -> None:
    """Remove do banco pedidos sem nome de vendedor (resquícios de execuções anteriores)."""
    with _conectar() as conn:
        resultado = conn.execute(
            "DELETE FROM pedidos WHERE ano_mes=? AND (nome_vendedor IS NULL OR TRIM(nome_vendedor)='')",
            (ano_mes,),
        )
        if resultado.rowcount:
            log.info("  DB → %d pedido(s) sem vendedor removido(s).", resultado.rowcount)


def limpar_mes_anterior(ano_mes_atual: str) -> None:
    """Remove pedidos de meses anteriores ao atual para manter o banco enxuto."""
    with _conectar() as conn:
        resultado = conn.execute(
            "DELETE FROM pedidos WHERE ano_mes != ?", (ano_mes_atual,)
        )
        if resultado.rowcount:
            log.info("  DB → %d pedidos de meses anteriores removidos.", resultado.rowcount)


# ═══════════════════════════════════════════════════════
#  CACHE DE EMPRESAS (bulk, TTL 7 dias)
# ═══════════════════════════════════════════════════════

def get_empresas() -> dict[int, str] | None:
    """
    Retorna dicionário completo {codigo: nome} se válido (< 7 dias).
    Retorna None se expirado ou vazio — sinal para buscar na API.
    """
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT codigo, nome, atualizado_em FROM cache_empresas"
        ).fetchall()

    if not rows:
        return None
    mais_antigo = min(r["atualizado_em"] for r in rows)
    if _expirado_dias(mais_antigo, TTL_EMPRESAS_D):
        return None
    return {r["codigo"]: r["nome"] for r in rows}


def set_empresas(empresas: dict[int, str]) -> None:
    """Substitui todo o cache de clientes de uma vez."""
    agora = datetime.now().isoformat(timespec="seconds")
    with _conectar() as conn:
        conn.execute("DELETE FROM cache_empresas")
        conn.executemany(
            "INSERT INTO cache_empresas VALUES (?, ?, ?)",
            [(codigo, nome, agora) for codigo, nome in empresas.items()],
        )
    log.info("  DB → %d clientes salvos no cache (TTL %d dias).", len(empresas), TTL_EMPRESAS_D)


def limpar_cache_empresas() -> None:
    """Força recarregamento na próxima execução apagando o cache de clientes."""
    with _conectar() as conn:
        conn.execute("DELETE FROM cache_empresas")
    log.info("  DB → cache de clientes limpo — será recarregado na próxima execução.")


# ═══════════════════════════════════════════════════════
#  CACHE DE VENDEDORES
# ═══════════════════════════════════════════════════════

def get_vendedores() -> dict[int, str] | None:
    """
    Retorna o dicionário completo {codigo: nome} se ainda válido (< 24h).
    Retorna None se o cache expirou ou está vazio.
    """
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT codigo, nome, atualizado_em FROM cache_vendedores"
        ).fetchall()

    if not rows:
        return None
    mais_antigo = min(r["atualizado_em"] for r in rows)
    if _expirado_horas(mais_antigo, TTL_VENDEDORES_H):
        return None
    return {r["codigo"]: r["nome"] for r in rows}


def set_vendedores(vendedores: dict[int, str]) -> None:
    agora = datetime.now().isoformat(timespec="seconds")
    with _conectar() as conn:
        conn.execute("DELETE FROM cache_vendedores")
        conn.executemany(
            "INSERT INTO cache_vendedores VALUES (?, ?, ?)",
            [(codigo, nome, agora) for codigo, nome in vendedores.items()],
        )
    log.info("  DB → %d vendedores salvos no cache (TTL %dh).", len(vendedores), TTL_VENDEDORES_H)


# ═══════════════════════════════════════════════════════
#  SYNC LOG
# ═══════════════════════════════════════════════════════

def registrar_sync(origem: str) -> None:
    agora = datetime.now().isoformat(timespec="seconds")
    with _conectar() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_log VALUES (?, ?)", (origem, agora)
        )


def ultimo_sync(origem: str) -> datetime | None:
    with _conectar() as conn:
        row = conn.execute(
            "SELECT ultimo_sync FROM sync_log WHERE origem=?", (origem,)
        ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["ultimo_sync"])


# ═══════════════════════════════════════════════════════
#  HELPERS INTERNOS
# ═══════════════════════════════════════════════════════

def _expirado_horas(iso_str: str, horas: int) -> bool:
    """Retorna True se o timestamp ISO tem mais de `horas` horas."""
    try:
        return datetime.now() - datetime.fromisoformat(iso_str) > timedelta(hours=horas)
    except Exception:
        return True


def _expirado_dias(iso_str: str, dias: int) -> bool:
    """Retorna True se o timestamp ISO tem mais de `dias` dias."""
    try:
        return datetime.now() - datetime.fromisoformat(iso_str) > timedelta(days=dias)
    except Exception:
        return True


def _pedido_para_row(p: Pedido, ano_mes: str) -> dict:
    return {
        "numero_pedido":      p.numero_pedido,
        "ano_mes":            ano_mes,
        "data_venda":         p.data_venda,
        "nome_cliente":       p.nome_cliente,
        "nome_vendedor":      p.nome_vendedor,
        "valor_pedido":       p.valor_pedido,
        "data_nota_fiscal":   p.data_nota_fiscal,
        "nota_fiscal":        p.nota_fiscal,
        "valor_faturado":     p.valor_faturado,
        "valor_pendente":     p.valor_pendente,
    }


def _row_para_pedido(row: sqlite3.Row) -> Pedido:
    return Pedido(
        data_venda            = row["data_venda"]            or "-",
        numero_pedido         = row["numero_pedido"],
        nome_cliente          = row["nome_cliente"]          or "",
        nome_vendedor         = row["nome_vendedor"]         or "",
        valor_pedido          = row["valor_pedido"]          or 0.0,
        data_nota_fiscal      = row["data_nota_fiscal"]      or "-",
        nota_fiscal           = row["nota_fiscal"]           or "-",
        valor_faturado        = row["valor_faturado"]        or 0.0,
        valor_pendente        = row["valor_pendente"]        or 0.0,
        comissao_vendedor_pct = row["comissao_vendedor_pct"] or 0.0,
        comissao_compras_pct  = row["comissao_compras_pct"]  or 0.0,
        comissao_menor_pct    = row["comissao_menor_pct"]    or 0.0,
        valor_comissao_menor  = row["valor_comissao_menor"]  or 0.0,
        obs_comissao          = row["obs_comissao"]          or "",
    )

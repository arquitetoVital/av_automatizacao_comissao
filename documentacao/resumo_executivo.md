# 📊 Sistema de Comissões — Resumo Executivo

**Status:** ✅ Funcional e otimizado  
**Linguagem:** Python 3.8+  
**Dependências:** python-dotenv, requests, pandas, openpyxl  
**Banco:** SQLite local com cache inteligente

---

## 🎯 Objetivo

Automatizar cálculo e distribuição de comissões de vendedores baseado em:
1. **Pedidos OMIE** (dados brutos)
2. **Simuladores de Custo** (Excel do vendedor)
3. **Validação de Compras** (Excel do comprador)

Resultado: Relatórios Excel prontos por vendedor e por coordenador.

---

## 🔄 Fluxo Visual

```
                              ┌─────────────────────────────────┐
                              │   OMIE API (HTTP)               │
                              │  ListarPedidos + ListarNF      │
                              └────────────┬────────────────────┘
                                           │
                         ┌─────────────────▼──────────────────┐
                         │ PASSO 1: Extração OMIE             │
                         │ • Cache bulk (Vendedores, Clientes)│
                         │ • Filtra cancelados/devolvidos     │
                         │ • Agrupa NFs por Pedido            │
                         │ • Resultado: 1847 pedidos [Pedido] │
                         └─────────────────┬──────────────────┘
                                           │
                 ┌─────────────────────────┼─────────────────────────┐
                 │                         │                         │
         ┌───────▼──────────┐      ┌───────▼──────────┐     ┌───────▼──────────┐
         │ Banco SQLite     │      │ Passo 2: Cálculo │     │ BlackList        │
         │ pedidos (upsert) │      │ • Lê simuladores │     │ • Ignora vendedores
         │ + cache (TTL)    │      │ • ThreadPool ×8  │     │ • Blacklist.txt  │
         └──────────────────┘      │ • min(vend,comp) │     └──────────────────┘
                 ▲                  └───────┬──────────┘
                 │                          │
                 └──────────────────────────┴──────────────────────┐
                                                                    │
                         ┌──────────────────────────────────────────▼────┐
                         │  PASTA_VENDEDOR_SP / PASTA_VENDEDOR_MG        │
                         │  N°XXXXX.xlsm (Simulador Vendedor)           │
                         │  Célula Z5: letra (A/B/C/D)                  │
                         │  Célula AB12: "Prejuízo" ou vazio            │
                         └─────────────────┬─────────────────────────────┘
                                           │
                         ┌─────────────────▼─────────────────┐
                         │ PASTA_COMPRADOR / MÊS / SP | MG   │
                         │ N°XXXXX OK.xlsm (Validado)        │
                         │ • Sem "OK" = Pendente Análise     │
                         │ • Com "OK" = Comissão Final       │
                         └─────────────────┬─────────────────┘
                                           │
              ┌────────────────────────────┼────────────────────────┐
              │                            │                        │
              ▼                            ▼                        ▼
      ┌──────────────┐          ┌──────────────────┐      ┌────────────────┐
      │ PASSO 3      │          │ PASSO 4          │      │ PASSO 5        │
      │ Relatório    │          │ Marcar Sem       │      │ Distribuição   │
      │ Coordenador  │          │ Simulador        │      │ por Vendedor   │
      │              │          │                  │      │                │
      │ Completo     │          │ NF + sem obs =   │      │ 1 Excel por    │
      │ (Comparativo)│          │ "Anexe..." obs   │      │ vendedor       │
      │              │          │                  │      │                │
      │ PASTA_COORD  │          │ (Interno)        │      │ PASTA_VENDEDOR │
      │ *.xlsx       │          │                  │      │ */MÊS/*.xlsx   │
      └──────────────┘          └──────────────────┘      └────────────────┘
```

---

## ⚙️ Componentes Principais

### **OmieClient** (clients.py)
```python
classe OmieClient:
  ├─ Cache bulk Vendedores (1x, TTL 24h)
  ├─ Cache bulk Empresas/Clientes (1x, TTL 7 dias)
  ├─ listar_pedidos()  → [dict] paginado com retry
  ├─ listar_nfs()      → [dict] paginado com retry
  └─ Nome vendedor / cliente → lookup direto em memória (O(1))
```

**Otimização:** Em vez de N chamadas HTTP por vendedor, 1 chamada lista 70 de uma vez.

### **Database** (database.py)
```python
SQLite comissoes.db:
  ├─ Tabela pedidos (numero_pedido + ano_mes como chave)
  │   └─ Campos: data, cliente, vendedor, valores, comissões, obs
  │
  ├─ Tabela cache_vendedores (Reutiliza em próxima execução)
  │   └─ TTL 24h
  │
  ├─ Tabela cache_empresas (Reutiliza em próxima execução)
  │   └─ TTL 7 dias
  │
  └─ Fingerprint de detecção de mudança
      └─ valor_pedido | valor_faturado | nota_fiscal
```

**Comportamento:** `upsert_pedidos()` preserva comissões ao atualizar.

### **Services** (services.py)
```
extrair_omie()
  ├─ Liga OmieClient
  ├─ Filtra cancelados/devolvidos/denegados
  ├─ Agrupa NFs por pedido
  └─ Retorna [Pedido] prontos

calcular_comissoes([Pedido])
  ├─ Descobre simuladores em PASTA_VENDEDOR
  ├─ Copia para PASTA_COMPRADOR (sem sobrescrever)
  ├─ Lê em paralelo (ThreadPoolExecutor ×8)
  ├─ Extrai Z5 (letra) + AB12 (status)
  ├─ Aplica tabela de comissão
  ├─ Se comprador ajustou: min(vend, compra)
  ├─ Se não ajustou: marca "Pendente"
  └─ Retorna [Pedido] com comissões preenchidas

marcar_sem_simulador([Pedido])
  └─ Pedidos com NF + sem obs = "Anexe simulador!"

pedidos_para_df([Pedido]) → pd.DataFrame
```

### **Reports** (reports.py)
```
gerar_relatorio_coordenador(df)
  ├─ Visão completa (14 colunas)
  ├─ Mostra comparativo (vend vs compra)
  ├─ Arquivo: PASTA_COORD/MÊS_RELATORIO_GERAL_COMISSAO.xlsx
  └─ 1 arquivo para todos

distribuir_para_vendedores(df)
  ├─ Classifica por vendedores_sp.txt / vendedores_mg.txt
  ├─ Visão simplificada (12 colunas, sem comparativo)
  ├─ Pasta destino: PASTA_VENDEDOR_*/NOME_VENDEDOR/MÊS/
  └─ 1 arquivo por vendedor

_escrever_excel(df, caminho)
  ├─ Salva em temp local
  ├─ Copia para destino final
  ├─ Se arquivo bloqueado (erro de permissão)
  │  └─ Fallback com timestamp: RELATORIO_20260405_123456.xlsx
  └─ Formata: header azul, cores alternadas, valores monetários
```

---

## 🔐 Configuração (Arquivo Único)

**config.py** = Fonte da verdade

```python
_MES = date(2026, 4, 1)  # ← ÚNICA linha a editar a cada mês

# Derivados automáticos:
MES_REF         = "04_ABRIL"
MES_INICIO_OMIE = "01/04/2026"
MES_FIM_OMIE    = "30/04/2026"
PASTA_CUSTO     = "CUSTO ABRIL"

# Pastas (de .env ou defaults):
PASTA_COORD       = Z:\...
PASTA_VENDEDOR_SP = Z:\...
PASTA_VENDEDOR_MG = Y:\...
PASTA_COMPRADOR   = Z:\...

# Tabela (rígida):
A: 2.0%,  B: 1.3%,  C: 0.7%,  D: 0.5%
```

---

## 📈 Exemplo: Saída do Sistema

### Log Console
```
════════════════════════════════════════════════
  GERADOR DE COMISSÕES  –  05/04/2026 10:30:15
  Mês: 04_ABRIL  (2026-04)
  OMIE: 01/04/2026 → 30/04/2026
════════════════════════════════════════════════
══ PASSO 1: extração OMIE ══
  142 vendedores carregados (cache 24h OK)
  8548 clientes carregados (cache 7d OK)
  3421 pedidos indexados (152 excluídos)
  1876 NFs → 1203 pedidos com NF
  Total: 1847 pedidos (1203 NF + 644 sem NF)

══ PASSO 2: comissões ══
  Simuladores: 156 SP + 87 MG = 243
  Lendo em paralelo...
  Resumo: 156 OK | 67 pendentes | 20 ignorados | 0 erros

══ PASSO 3: relatório coordenador ══
  Salvo: Z:\...\04_RELATORIO_GERAL_COMISSAO.xlsx (1847 linhas)

══ PASSO 4: marcar sem simulador ══
  67 pedidos faturados sem simulador

══ PASSO 5: distribuição ══
  ABNER_LUIS_CARDOSO_RODRIGUES → 24 pedidos [SP]
  ANTONIO_PAIVA → 18 pedidos [SP]
  EDUARDO_VITAL → 22 pedidos [MG]
  ...
  [AVISO] 3 vendedores sem filial definida (não em vendedores_*.txt)

════════════════════════════════════════════════
✅ Concluído em 87.3 segundos
📄 Log: /projeto/gerar_comissoes.log
════════════════════════════════════════════════
```

### Excel Gerado (Coordenador)
| Data_Venda | Numero_Pedido | Nome_Cliente | Comissao_Vendedor_% | Comissao_Compras_% | Menor_Comissao_% | Valor_Comissao_Calculado | Obs_Comissao |
|---|---|---|---|---|---|---|---|
| 05/04/2026 | 001234 | EMPRESA X | 2.00% | 2.00% | 2.00% | 2,400.00 | Comissão Definida! |
| 05/04/2026 | 001235 | EMPRESA Y | 2.00% | — | — | 0.00 | Análise de Compras Pendente |
| 10/04/2026 | 001236 | EMPRESA Z | 2.00% | 2.00% | 2.00% | 0.00 | Comissão Definida! - Prejuízo |

---

## 🚀 Como Rodar

### Primeira Vez
```bash
# 1. Renomear .env
mv _env .env

# 2. Editar credenciais
nano .env
# OMIE_APP_KEY=sua_chave
# OMIE_APP_SECRET=seu_secret

# 3. Instalar deps
pip install python-dotenv requests pandas openpyxl

# 4. Rodar
python main.py
```

### Próximos Meses
```bash
# Editar 1 linha em config.py:
_MES = date(2026, 5, 1)  # novo mês

# Rodar
python main.py
```

### Reset Total
```bash
# Apaga banco (força recarregamento de cache):
rm comissoes.db
python main.py
```

---

## ✅ Checklist de Execução

- [ ] `_MES` em config.py está correto (novo mês)?
- [ ] `.env` existe com credenciais OMIE válidas?
- [ ] Pastas de rede (Z:, Y:) estão mapeadas e acessíveis?
- [ ] `blacklist.txt` está atualizado (vendedores a ignorar)?
- [ ] `vendedores_sp.txt` e `vendedores_mg.txt` têm todos os vendedores?
- [ ] Ninguém está com Excel aberto (vai bloquear escrita)?
- [ ] Há espaço em disco para banco + logs (geralmente <10MB)?

---

## ⚠️ Potenciais Falhas

| Sinal | Causa Provável | Ação |
|-------|---|---|
| `KeyError: OMIE_APP_KEY` | .env não existe ou .env não carregado | `mv _env .env` + editar |
| `PermissionError` ao salvar Excel | Arquivo aberto em outro lugar | Fechar Excel e reexecutar |
| `[AVISO] Falha ao buscar página X` | Erro no OMIE (pode ser rate limit) | Aguarde e retente; cheque credenciais |
| 0 vendedores em log | Credenciais OMIE erradas | Validar APP_KEY/SECRET |
| Vendedor sem relatório gerado | Não em vendedores_sp.txt ou _mg.txt | Adicionar à lista correta |
| `[SKIP] Pedido XXX sem vendedor` | Pedido órfão no OMIE | Verificar OMIE; será salvo com comissão 0 |

---

## 📊 Estatísticas Típicas

**Período:** Um mês  
**Pedidos OMIE:** ~3400 (151 excluídos)  
**NFs:** ~1900  
**Pedidos únicos:** ~1850  
**Simuladores encontrados:** ~240  
**Comissões OK:** ~160  
**Pendentes (sem comprador):** ~70  
**Tempo execução:** 80-120 segundos  

---

## 🔧 Dependências

| Pacote | Uso |
|--------|-----|
| `python-dotenv` | Carrega .env |
| `requests` | HTTP para OMIE + retry |
| `pandas` | DataFrames |
| `openpyxl` | Escrita Excel |
| `sqlite3` | Banco (stdlib) |
| `threading` | Lock para cache (stdlib) |
| `concurrent.futures` | ThreadPoolExecutor (stdlib) |

---

## 🎓 Próximos Passos (Opcional)

1. **Auditoria:** Registrar quem mudou o quê e quando
2. **Alertas:** Notificar vendedores quando comissão pronta
3. **Dashboard:** Visualizar comissões em tempo real (web)
4. **API REST:** Consultar comissões via API
5. **Comparativo:** Mês vs Mês (crescimento, queda)

---

## 📞 Suporte

- **Logs detalhados:** `gerar_comissoes.log` (DEBUG+INFO)
- **Banco:** `comissoes.db` (SQLite, inspecionável com `sqlite3`)
- **Config centralizada:** Tudo em `config.py`
- **Código bem documentado:** Docstrings + comentários inline

---

**Última atualização:** 05/04/2026  
**Versão:** 1.0 (Estável)

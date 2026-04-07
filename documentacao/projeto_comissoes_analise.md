# 📊 Sistema de Geração de Comissões — Análise Completa

**Última atualização:** Abril 2026  
**Status:** ✅ Funcional com arquitetura robusta  
**Objetivo:** Automatizar cálculo de comissões de vendas a partir de dados OMIE com validação de compras

---

## 📁 Estrutura do Projeto

```
projeto_comissoes/
├── main.py                  # Orquestração dos 5 passos
├── config.py                # Configurações centralizadas (datas, pastas, credenciais)
├── clients.py               # Integração OMIE (API + cache bulk)
├── database.py              # Persistência SQLite com TTL de cache
├── services.py              # Lógica de negócio (extração, comissões, leitura Excel)
├── models.py                # Estruturas de dados (Pedido, InfoCusto)
├── reports.py               # Geração de planilhas Excel
├── utils.py                 # Funções utilitárias
├── blacklist.txt            # Vendedores a ignorar
├── vendedores_sp.txt        # Lista de vendedores SP (para distribuição)
├── vendedores_mg.txt        # Lista de vendedores MG (para distribuição)
├── .env                     # Credenciais OMIE (não commitar)
├── .gitignore               # Controle de versionamento
├── comissoes.db             # Banco SQLite (gerado automaticamente)
└── gerar_comissoes.log      # Log de execução
```

---

## 🔄 Fluxo de Execução (5 Passos)

### **Passo 1: Extração OMIE**
**Função:** `services.extrair_omie()`

1. **ListarPedidos (paginado)** → Busca todos os pedidos do período
   - Filtra: cancelados, devolvidos, denegados
   - Campo `dInc` usado para filtrar por mês de inclusão
2. **ListarNF (paginado)** → Busca todas as NFs emitidas no período
3. **Dois grupos:** 
   - **Grupo A:** Pedidos com NF associada (direto do OMIE)
   - **Grupo B:** Pedidos sem NF, mas incluídos no mês (inclui previsões)
4. **Resultado:** Lista `[Pedido]` com dados brutos (sem comissão ainda)

**Cache aplicado:**
- Vendedores: carregados 1x em bulk na __init__ (TTL 24h)
- Empresas (clientes): carregados 1x em bulk na __init__ (TTL 7 dias)

---

### **Passo 2: Cálculo de Comissões**
**Função:** `services.calcular_comissoes(pedidos)`

1. **Descoberta de simuladores:**
   - SP: `PASTA_VENDEDOR_SP/*/CUSTO MÊS/N°XXX.xlsm` → vendedor
   - MG: `PASTA_VENDEDOR_MG/*/CUSTO MÊS/N°XXX.xlsm` → vendedor
   - Filtra por blacklist (ignorar certos vendedores)

2. **Cópia para comprador:**
   - Copia simuladores do vendedor → `PASTA_COMPRADOR/MÊS/SP` e `/MG`
   - Só copia se não existir versão OK

3. **Leitura em paralelo:**
   - ThreadPoolExecutor com 8 threads
   - Lê célula **Z5** (letra de comissão: A, B, C, D)
   - Lê célula **AB12** (status: "Prejuízo" ou normal)

4. **Aplicação de comissão:**
   - Se comprador ajustou (tem OK): usa `min(vendedor, comprador)`
   - Se não ajustou: marca como "Análise de Compras Pendente"
   - Se status = "Prejuízo": comissão = 0%
   - Tabela: A=2%, B=1.3%, C=0.7%, D=0.5%

5. **Resultado:** Comissões persistidas no DB

---

### **Passo 3: Relatório Coordenador**
**Função:** `reports.gerar_relatorio_coordenador(df)`

- **Destino:** `PASTA_COORD/MÊS_RELATORIO_GERAL_COMISSAO.xlsx`
- **Colunas:** Data, Pedido, Cliente, Vendedor, Valor, NF, Comissão Vendedor %, Comissão Compras %, Comissão Final %, Valor Calculado, Observações
- **Público:** Coordenador (visão 360° com comparativo)

---

### **Passo 4: Marcação de Sem Simulador**
**Função:** `services.marcar_sem_simulador(pedidos)`

- Identifica pedidos faturados (com NF) sem simulador
- Marca com obs: **"Anexe o simulador de custo!"**
- Pedidos sem NF ficam sem obs (ainda não faturados)

---

### **Passo 5: Distribuição para Vendedores**
**Função:** `reports.distribuir_para_vendedores(df)`

- **Destino por filial:**
  - SP: `PASTA_VENDEDOR_SP/NOME_VENDEDOR/MÊS/MÊS_COMISSAO_NOME.xlsx`
  - MG: `PASTA_VENDEDOR_MG/NOME_VENDEDOR/MÊS/MÊS_COMISSAO_NOME.xlsx`
- **Colunas:** Simplificadas (sem comparativo de comissões)
- **Público:** Cada vendedor recebe apenas seus pedidos

---

## 🗄️ Banco de Dados (SQLite)

**Arquivo:** `comissoes.db`

### Tabelas

#### **pedidos**
```sql
numero_pedido, ano_mes → PRIMARY KEY
data_venda, nome_cliente, nome_vendedor, valor_pedido,
data_nota_fiscal, nota_fiscal, valor_faturado, valor_pendente,
comissao_vendedor_pct, comissao_compras_pct,
comissao_menor_pct, valor_comissao_menor,
obs_comissao, atualizado_em (ISO 8601)
```

**Lógica:**
- `upsert_pedidos()`: INSERT OR REPLACE, preserva comissões ao atualizar
- Fingerprint: `valor_pedido|valor_faturado|nota_fiscal` detecta mudanças
- Limpa meses antigos automaticamente no inicio

#### **cache_vendedores**
```sql
codigo → PRIMARY KEY
nome, atualizado_em
```
- TTL: 24h (recarrega se expirado)

#### **cache_empresas**
```sql
codigo → PRIMARY KEY
nome, atualizado_em
```
- TTL: 7 dias (recarrega se expirado)

#### **sync_log**
```sql
origem → PRIMARY KEY
ultimo_sync (ISO 8601)
```

---

## 🔐 Configuração e Credenciais

### **config.py** (centraliza tudo)

```python
# Editar 1 linha apenas:
_MES = date(2026, 4, 1)  # ← muda mês de referência

# Derivados automáticos:
MES_REF           = "04_ABRIL"
MES_INICIO_OMIE   = "01/04/2026"
MES_FIM_OMIE      = "30/04/2026"
PASTA_CUSTO       = "CUSTO ABRIL"

# Pastas (de .env ou defaults):
PASTA_COORD, PASTA_VENDEDOR_SP/MG, PASTA_COMPRADOR

# Tabela de comissão (rígida):
A: 2%, B: 1.3%, C: 0.7%, D: 0.5%
```

### **.env** (credenciais)
```
OMIE_APP_KEY=4011885988110
OMIE_APP_SECRET=415133ab4e1db4cf532665301496e0f3

# Opcional — sobrescreve config.py:
PASTA_COORD=...
PASTA_VENDEDOR_SP=...
```

**Regra:** Nunca commitar .env no git (está em .gitignore)

---

## 📋 Listas de Controle

### **blacklist.txt**
Vendedores ou pastas a ignorar completamente:
```
AÇOS_VITAL                              # exemplo: fornecedor, não vendedor
EUVERALDO_OLIVEIRA_DE_SOUZA            # ex-funcionário, apenas MG
JOAO_VITOR_MARTINS
```
- Ignorados na **Passo 1** (extração) e **Passo 2** (comissões)
- Formato: nome de pasta (underscores, maiúsculo)
- Sem mexer no código — edite apenas o arquivo

### **vendedores_sp.txt / vendedores_mg.txt**
Classificação de vendedores por filial:
```
ABNER_LUIS_CARDOSO_RODRIGUES
ANTONIO_PAIVA
DANIEL_SOUZA_DA_SILVA
...
```
- Usado no **Passo 5** (distribuição) para definir pasta de destino
- Vendedores não listados: aviso no log, sem relatório gerado

---

## 🎯 Destaques da Arquitetura

### ✅ **Otimizações Implementadas**

1. **Cache Bulk com TTL**
   - 1 chamada ListarVendedores (~70 itens) em vez de N chamadas
   - 1 chamada ListarEmpresas (~8500 itens) em vez de M chamadas
   - Thread-safe com `threading.Lock`

2. **Leitura Paralela (ThreadPoolExecutor)**
   - 8 threads simultâneas para ler Excel do vendedor + comprador
   - ZIP parsing + XML direto (sem openpyxl pesado)
   - Isolamento de erros (falha de 1 arquivo não interrompe resto)

3. **Fingerprint Inteligente**
   - Detecta mudanças em valor_pedido | valor_faturado | nota_fiscal
   - Preserva comissões calculadas ao atualizar outros campos

4. **Tratamento de Erros Resiliente**
   - Páginas com erro em ListarPedidos/ListarNF: puladas com aviso
   - Resultado pode estar incompleto (log registra quais páginas falharam)
   - Falha crítica em comissões: aborta com `sys.exit(1)`

5. **Excel com Bloqueio Inteligente**
   - Salva em temp local → copia para destino
   - Se bloqueado (arquivo aberto): fallback com timestamp
   - Exemplo: `RELATORIO_GERAL_20260405_123456.xlsx`

### ⚠️ **Pontos de Atenção**

1. **Dependência de Pasta de Rede**
   - Assume Z:\ (SP) e Y:\ (MG) mapeados
   - Se desconectar: falha silenciosa (copiar, ler simulador)
   - Solution: monitorar logs para `[ERRO]`

2. **Formato de Arquivo Simulador**
   - Célula **Z5** = letra de comissão (A/B/C/D)
   - Célula **AB12** = status ("Prejuízo" ou vazio)
   - Se não encontrar: comissão = 0%, status = "Análise de Compras Pendente"

3. **Blacklist vs Vendedores_sp.txt / vendedores_mg.txt**
   - **Blacklist:** Ignora completamente (nem entra na extração)
   - **Não em vendedores_*.txt:** Aviso no log, relatório não gerado

4. **Múltiplas NFs por Pedido**
   - Um pedido pode ter N NFs
   - Cada NF = 1 linha no relatório (mesma comissão)
   - Valor faturado = soma todas as NFs do pedido

---

## 🚀 Como Executar

### **Primeira Vez**
```bash
# 1. Renomear .env
mv _env .env
# Editar com credenciais reais

# 2. Instalar dependências
pip install python-dotenv requests pandas openpyxl

# 3. Rodar
python main.py
```

### **Execuções Normais (mês a mês)**
```bash
# 1. Editar config.py apenas:
_MES = date(2026, 5, 1)  # novo mês

# 2. Rodar
python main.py
```

### **Resetar Tudo**
```bash
# Apagar banco (força recarregamento de cache + pedidos)
rm comissoes.db
python main.py
```

---

## 📊 Exemplo de Saída

### Log Console
```
════════════════════════════════════════════════
  GERADOR DE COMISSÕES  –  05/04/2026 10:30:00
  Mês de referência : 04_ABRIL  (2026-04)
  OMIE período      : 01/04/2026 → 30/04/2026
════════════════════════════════════════════════
══ PASSO 1: extração OMIE ══
  OMIE → 142 vendedores carregados do cache DB.
  OMIE → 8548 clientes carregados do cache DB.
  OMIE → 2341 pedidos listados no período.
  OMIE → 1876 NFs listadas no período.
  Blacklist carregada: 7 entradas.
  3421 pedidos indexados (152 excluídos).
  1876 NFs → 1203 pedidos únicos com NF.
  Total: 1847 pedidos (1203 com NF + 644 sem NF)

══ PASSO 2: comissões ══
  Simuladores encontrados: 156 SP + 87 MG = 243 total.
  Simuladores [SP] → comprador: 45 copiados | 111 já existiam.
  Simuladores [MG] → comprador: 32 copiados | 55 já existiam.
  Lendo em paralelo: 243 simuladores do vendedor + 124 do comprador…
  [OK] Pedido 001234 (2 linha(s)) → Comissão Definida!
  [OK] Pedido 001235 (1 linha(s)) → Comissão Definida! - Prejuízo
  [PENDENTE] Pedido 001236 (3 linha(s))
  ...
  Resumo: 156 OK | 67 pendentes | 20 ignorados | 0 erros
  DB → comissões persistidas (1847 pedidos).

══ PASSO 3: relatório coordenador ══
  Arquivo salvo: Z:\...\04_RELATORIO_GERAL_COMISSAO.xlsx  (1847 linhas)

══ PASSO 4: marcando sem simulador ══
  67 pedido(s) faturado(s) sem simulador.

══ PASSO 5: distribuição ══
  Vendedores: 156
  'ABNER_LUIS_CARDOSO_RODRIGUES' → 24 pedidos  [SP]
  'ANTONIO_PAIVA' → 18 pedidos  [SP]
  'EDUARDO_VITAL' → 22 pedidos  [MG]
  ...
  Distribuição concluída.

════════════════════════════════════════════════
  ✅ Concluído em 87.3s
  📄 Log: /projeto/gerar_comissoes.log
════════════════════════════════════════════════
```

---

## 🔍 Troubleshooting

| Problema | Causa | Solução |
|----------|-------|---------|
| `KeyError: OMIE_APP_KEY` | .env não criado ou .env não carregado | `mv _env .env && editar com credenciais` |
| `PermissionError` ao salvar Excel | Arquivo aberto em outro lugar | Fecha arquivo ou aguarde fallback com timestamp |
| `[AVISO] Falha ao buscar pedidos página X` | Erro no OMIE | Valida credenciais; pode ser rate limit; retenta próx vez |
| `0 vendedores carregados` | Credenciais erradas | Valida APP_KEY e APP_SECRET no OMIE |
| Vendedor sem relatório | Não está em vendedores_sp.txt ou _mg.txt | Adiciona a um dos arquivos |
| `[SKIP] Pedido XXX sem vendedor` | Pedido órfão no OMIE | Verifica OMIE; pedido será salvo com comissão=0 |

---

## 📈 Possíveis Melhorias Futuras

1. **API para consulta de comissões** em tempo real
2. **Dashboard** de monitoramento mensal
3. **Webhooks** para alertar vendedores quando relatório pronto
4. **Histórico comparativo** (Mês A vs Mês B)
5. **Simulador embutido** (eliminar dependência de Excel do comprador)
6. **Bulk SMS/Email** notificação automática

---

## 📝 Resumo de Dependências

```
python-dotenv     → Carrega credenciais de .env
requests          → HTTP para OMIE + retry automático
pandas            → Manipulação de DataFrames
openpyxl          → Escrita de Excel
zipfile, xml.etree → Leitura otimizada de Excel (ZIP + XML interno)
sqlite3           → Banco de dados local (stdlib)
threading         → Cache thread-safe (stdlib)
concurrent.futures → ThreadPoolExecutor (stdlib)
```

---

## 📞 Contato / Suporte

- **Logs:** `gerar_comissoes.log` (DEBUG + INFO)
- **Banco:** `comissoes.db` (SQLite, pode ser inspecionado com `sqlite3 comissoes.db`)
- **Credenciais:** Validar em https://app.omie.com.br (credentials)

---

**Última atualização:** 05 de abril de 2026

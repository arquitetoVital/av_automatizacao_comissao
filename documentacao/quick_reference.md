# ⚡ Quick Reference — Sistema de Comissões

---

## 🚀 Para Rodar (Semanalmente/Mês a Mês)

```bash
# ✅ Rodar normal:
python main.py

# ✅ Resetar banco (força recarregar cache + pedidos):
rm comissoes.db && python main.py

# ✅ Ver log em tempo real:
tail -f gerar_comissoes.log

# ✅ Limparz (remove DB + logs antigos):
rm comissoes.db gerar_comissoes.log && python main.py
```

---

## 📝 Antes de Cada Execução

### Checklist

- [ ] **Editar `config.py`** — linha 1:
  ```python
  _MES = date(2026, 5, 1)  # ← novo mês
  ```

- [ ] **Verificar `.env`** — credenciais OMIE válidas?
  ```bash
  cat .env
  ```

- [ ] **Atualizar `blacklist.txt`** (se houver novos vendedores a ignorar)

- [ ] **Atualizar `vendedores_sp.txt` + `vendedores_mg.txt`** (novo vendedor? Adicionar)

- [ ] **Fechar Excel** — nenhum .xlsx aberto (evita PermissionError)

- [ ] **Pastas de rede acessíveis?** 
  ```bash
  dir Z:\Vendas_Acos-Vital\Vendas\2026  # Windows
  ls /Volumes/Z/Vendas_Acos-Vital/...  # macOS
  ```

---

## 🔧 Estrutura Arquivo por Arquivo

### **config.py** — EDITAR AQUI
```python
_MES = date(2026, 4, 1)        # ← Único lugar a mudar

# Resto é automático:
MES_REF = "04_ABRIL"           # Derivado
MES_INICIO_OMIE = "01/04/2026" # Derivado
MES_FIM_OMIE = "30/04/2026"    # Derivado

# Pastas (de .env):
PASTA_COORD = Path("Z:\...\RELATORIO GERAL")
PASTA_VENDEDOR_SP = Path("Z:\...\2026")
PASTA_VENDEDOR_MG = Path("Y:\...\2026")

# Tabela rígida:
TABELA_COMISSAO = {"A": 0.02, "B": 0.013, "C": 0.007, "D": 0.005}
```

### **blacklist.txt** — EDITAR AQUI
```
# Um nome por linha, maiúsculo, underscores
AÇOS_VITAL
EUVERALDO_OLIVEIRA_DE_SOUZA
JOAO_VITOR_MARTINS
```

### **vendedores_sp.txt** — EDITAR AQUI
```
# SP filial — um nome por linha
ABNER_LUIS_CARDOSO_RODRIGUES
ANTONIO_PAIVA
DANIEL_SOUZA_DA_SILVA
...
```

### **vendedores_mg.txt** — EDITAR AQUI
```
# MG filial — um nome por linha
EDUARDO_VITAL
HUGO_DOS_SANTOS_GONÇALVES
...
```

### **main.py** — RODAR (não editar)
```python
# Orquestra 5 passos em sequência:
1. extrair_omie() → lista [Pedido]
2. calcular_comissoes() → aplica lógica
3. gerar_relatorio_coordenador() → Excel completo
4. marcar_sem_simulador() → obs auxiliares
5. distribuir_para_vendedores() → Excel por vendedor
```

### **services.py** — Lógica principal (não mexer)
```python
extrair_omie()           # OMIE → [Pedido]
calcular_comissoes()     # Simul. → comissões
marcar_sem_simulador()   # NF + sem obs → obs
pedidos_para_df()        # [Pedido] → DataFrame
```

### **clients.py** — API OMIE (não mexer)
```python
OmieClient.nome_vendedor()     # Lookup (cache)
OmieClient.consultar_cliente() # Lookup (cache)
OmieClient.listar_pedidos()    # HTTP paginado
OmieClient.listar_nfs()        # HTTP paginado
```

### **database.py** — Banco (não mexer)
```python
inicializar()              # Cria tabelas
upsert_pedidos()           # Insert/Update com fingerprint
carregar_pedidos()         # Carrega do mês
atualizar_comissoes()      # Persist comissões
get_vendedores()           # Cache (TTL 24h)
get_empresas()             # Cache (TTL 7 dias)
```

### **reports.py** — Excel (não mexer)
```python
gerar_relatorio_coordenador()  # Visão 360°
distribuir_para_vendedores()   # Visão individual
```

### **.env** — NÃO COMMITAR
```
OMIE_APP_KEY=4011885988110
OMIE_APP_SECRET=415133ab4e1db4cf532665301496e0f3
```

---

## 📊 Fluxo dos 5 Passos

```
┌─ PASSO 1: extrair_omie() ──────────────────────────────┐
│ • ListarPedidos OMIE (paginado)                        │
│ • ListarNF OMIE (paginado)                             │
│ • Filtra: cancelados, devolvidos, denegados           │
│ • Agrupa NFs por pedido                               │
│ Saída: [Pedido] com dados brutos (sem comissão)       │
└────────────────┬─────────────────────────────────────────┘
                 │ pedidos = [Pedido]
                 ▼
┌─ PASSO 2: calcular_comissoes() ───────────────────────┐
│ • Descobre N°XXXXX.xlsm em PASTA_VENDEDOR_SP/_MG      │
│ • Copia para PASTA_COMPRADOR (sem sobrescrever)       │
│ • ThreadPool ×8 lê: Z5 (letra) + AB12 (status)        │
│ • Aplica tabela: A=2%, B=1.3%, C=0.7%, D=0.5%        │
│ • Se comprador OK: min(vend, compra)                  │
│ Saída: [Pedido] com comissão_* preenchidas           │
└────────────────┬─────────────────────────────────────────┘
                 │
         ┌───────┴────────┐
         ▼                ▼
    ┌─ PASSO 3 ─┐   ┌─ PASSO 4 ──────────────┐
    │ Coordenador│   │ Marcar sem Simulador   │
    │ Excel:     │   │ NF + sem obs =         │
    │ Completo   │   │ "Anexe simulador!"     │
    │ (14 cols)  │   │ (Interno)              │
    │ 1 arquivo  │   └────────────┬───────────┘
    └─────┬──────┘                │
          │                       ▼ df reconvertido
          │              ┌─ PASSO 5 ──────────┐
          │              │ Distribuição       │
          │              │ Por Vendedor       │
          │              │ Excel simplificado │
          │              │ (12 colunas)       │
          │              │ 1 arquivo/vend     │
          │              └────────────────────┘
          │
     ┌────▼──────────────────────────────────────┐
     │   OUTPUTS (Pastas de Rede)                │
     │ • PASTA_COORD/MÊS_RELATORIO_GERAL_*.xlsx │
     │ • PASTA_VENDEDOR_*/NOME/MÊS/*.xlsx        │
     └─────────────────────────────────────────┘
```

---

## 🎯 Interpretando o Log

### ✅ Sucesso
```
══ PASSO 1: extração OMIE ══
  OMIE → 142 vendedores carregados do cache DB.
  OMIE → 8548 clientes carregados do cache DB.
  Total: 1847 pedidos (1203 com NF + 644 sem NF)

══ PASSO 2: comissões ══
  Resumo: 156 OK | 67 pendentes | 20 ignorados | 0 erros

✅ Concluído em 87.3s
```

### ⚠️ Aviso (mas continua)
```
[AVISO] Falha ao buscar pedidos página 5: [erro]...
resultado pode estar incompleto.

[AVISO] 3 vendedor(es) sem filial definida:
  – JOAO_SILVA
  – MARIA_SANTOS
```

### ❌ Erro Crítico (aborta)
```
Falha crítica no cálculo de comissões: ... — abortando execução.
Traceback: ...
```
→ Solução: Checar log completo, validar .env, resetar DB.

---

## 📋 Guia de Troubleshooting

| Erro | Causa | Solução |
|------|-------|---------|
| `KeyError: OMIE_APP_KEY` | Falta .env | `mv _env .env` + editar |
| `PermissionError` ao escrever Excel | Arquivo aberto | Fechar Excel, reexecuta |
| `0 clientes carregados` | Credenciais OMIE erradas | Validar APP_KEY/SECRET |
| `[SKIP] Pedido sem vendedor` | Pedido órfão no OMIE | Normal — será ignorado |
| Vendedor sem relatório | Não em vendedores_*.txt | Adicionar à lista correta |
| Banco "travo" (slow) | DB cresce muito | `rm comissoes.db` + reexecuta |

---

## 💾 Arquivos Gerados

### Saídas (Excel)
```
Z:\...\RELATORIO GERAL\
  ├─ 04_RELATORIO_GERAL_COMISSAO.xlsx      # ← Coordenador (1847 linhas)
  └─ 04_RELATORIO_GERAL_COMISSAO_20260405_123456.xlsx  # Se bloqueado

Z:\Vendas_Acos-Vital\Vendas\2026\
  ├─ ABNER_LUIS_CARDOSO_RODRIGUES\04_ABRIL\
  │  └─ 04_COMISSAO_ABNER_LUIS.xlsx       # ← SP
  ├─ ANTONIO_PAIVA\04_ABRIL\
  │  └─ 04_COMISSAO_ANTONIO_PAIVA.xlsx    # ← SP
  └─ ...

Y:\Vendas_Acos-Vital\Vendas\2026\
  ├─ EDUARDO_VITAL\04_ABRIL\
  │  └─ 04_COMISSAO_EDUARDO_VITAL.xlsx    # ← MG
  └─ ...
```

### Internos (Banco + Log)
```
/projeto/
  ├─ comissoes.db           # SQLite (gerado, pode deletar para reset)
  ├─ gerar_comissoes.log    # Log de execução (DEBUG+INFO)
  └─ main.py                # Rodar daqui
```

---

## 🔄 Fluxo de Manutenção (Mensal)

```
1º dia do mês:
  ├─ Editar config.py: _MES = date(2026, 5, 1)
  ├─ Verificar blacklist.txt (novos para ignorar?)
  ├─ Verificar vendedores_sp.txt + vendedores_mg.txt (novos?)
  └─ python main.py

Resultado:
  ├─ Z:\...\RELATORIO_GERAL_COMISSAO.xlsx  → Coordenador
  ├─ Z:\VENDEDORES_SP\...\*.xlsx            → Vendedores SP
  ├─ Y:\VENDEDORES_MG\...\*.xlsx            → Vendedores MG
  └─ gerar_comissoes.log                    → Verificar OK

3-5 dias depois:
  ├─ Comprador analisa simuladores
  ├─ Renomeia: N°000001.xlsm → N°000001 OK.xlsm
  └─ Copiar para PASTA_COMPRADOR (automático no passo 2)

Final do mês:
  └─ Pagamentos processados com base em relatório
```

---

## 🎓 Conceitos-Chave

### **Fingerprint**
```python
# Detecta se pedido mudou (valor, NF, faturamento)
valor_pedido | valor_faturado | nota_fiscal

# Se mudou: UPDATE (preserva comissão)
# Se não mudou: SKIP (evita reprocessar)
```

### **Cache com TTL**
```python
# Primeira execução: busca API OMIE (ListarVendedores, ListarClientes)
# Próximas execuções (< 24h/7d): usa cache DB

# Vantagem: 1 chamada em vez de 8500
# Desvantagem: novo vendedor aparece só amanhã
```

### **Comissão Definida vs Pendente**
```python
# DEFINIDA: Comprador revisou (tem OK)
#   → min(vendedor, comprador)

# PENDENTE: Comprador ainda não viu
#   → 0% (obs: "Análise de Compras Pendente")

# PREJUÍZO: Status AB12 = "Prejuízo"
#   → 0% (obs: "Comissão Definida! - Prejuízo")
```

### **Blacklist vs Lista de Filial**
```python
# BLACKLIST (blacklist.txt)
#   → Ignora COMPLETAMENTE (não entra nem na extração)
#   → Uso: fornecedores, ex-funcionários

# LISTA DE FILIAL (vendedores_sp.txt / _mg.txt)
#   → Classifica para qual pasta distribuir
#   → Não estar na lista = aviso no log, sem relatório
```

---

## 🔐 Segurança (Não Fazer!)

```bash
# ❌ NUNCA commitar:
.env                          # Credenciais

# ❌ NUNCA editar em código:
OMIE_APP_KEY = "chave aqui"  # Sempre via .env

# ❌ NUNCA rodar em background sem monitorar:
nohup python main.py &        # Sem acompanhamento de erro
```

---

## 📊 Métricas de Performance

| Operação | Tempo Típico |
|----------|---|
| ListarPedidos (OMIE) | 10-15s |
| ListarNF (OMIE) | 8-12s |
| Leitura paralela simuladores (8 threads) | 20-30s |
| Geração Excel coordenador (1800 linhas) | 3-5s |
| Geração Excel vendedores (156 arquivos) | 10-15s |
| **TOTAL** | **80-120s** |

---

## 📚 Comandos Úteis

```bash
# Ver log em tempo real:
tail -f gerar_comissoes.log

# Ver últimas 50 linhas:
tail -50 gerar_comissoes.log

# Buscar erros no log:
grep -i "erro\|falha\|crítico" gerar_comissoes.log

# Contar linhas processadas:
grep "inseridos\|atualizados" gerar_comissoes.log

# Inspecionar banco SQLite:
sqlite3 comissoes.db "SELECT COUNT(*) FROM pedidos WHERE ano_mes='2026-04';"

# Resetar banco:
rm comissoes.db

# Resetar tudo:
rm comissoes.db gerar_comissoes.log
```

---

## 🎯 Resumo Ultra-Rápido

| Passos | Quando | Ação |
|--------|--------|------|
| **Antes de rodar** | 1x/mês | Editar `config.py`: `_MES = date(2026, X, 1)` |
| **Manutenção de dados** | Conforme precisa | Editar `blacklist.txt`, `vendedores_*.txt` |
| **Execução** | 1x/mês | `python main.py` |
| **Resultado** | Automático | Excel em Z:\ e Y:\ |
| **Reset** | Se travar | `rm comissoes.db && python main.py` |

---

**Última atualização:** 05/04/2026

# 🎯 Sistema de Geração de Comissões — README

> **Documentação Completa do Projeto Python para Cálculo Automático de Comissões de Vendedores**

---

## 📚 Documentação Criada

Você recebeu **5 documentos completos** (2000+ linhas) que cobrem todos os aspectos do projeto:

| Documento | Páginas | Perfil | Tempo |
|-----------|---------|--------|-------|
| 📄 **indice_documentacao.md** | 1 | Guia de navegação | 5 min |
| 📄 **resumo_executivo.md** | 14 | Não-técnico / Iniciante | 20 min |
| 📄 **projeto_comissoes_analise.md** | 13 | Analista / Arquiteto | 40 min |
| 📄 **desenvolvimento_tecnico.md** | 15 | Desenvolvedor | 60 min |
| 📄 **quick_reference.md** | 12 | Referência rápida (cola) | 5 min |

**Total:** ~2.000 linhas, 66 KB

---

## 🚀 Comece Aqui

### **Você É Um...**

#### 👤 **Usuário que Precisa Rodar (1-2x por mês)?**
```
1. Leia: resumo_executivo.md (20 min)
2. Consulte: quick_reference.md (conforme precisa)
3. Execute: python main.py
```

#### 📊 **Analista Entendendo o Projeto?**
```
1. Leia: resumo_executivo.md (20 min)
2. Leia: projeto_comissoes_analise.md (40 min)
3. Consulte: quick_reference.md (quando tiver dúvida)
```

#### 💻 **Desenvolvedor Modificando o Código?**
```
1. Leia TODOS em ordem:
   a) resumo_executivo.md
   b) projeto_comissoes_analise.md
   c) desenvolvimento_tecnico.md
   d) quick_reference.md
```

#### 🔧 **Ajudando a Resolver um Erro?**
```
1. Consulte: quick_reference.md → "Guia de Troubleshooting"
2. Se não resolver: leia projeto_comissoes_analise.md (seção relevante)
```

---

## 🎯 O Que o Sistema Faz

```
OMIE API
   ↓
[Extrai 3400+ pedidos do mês]
   ↓
[Lê 240+ simuladores Excel (paralelo)]
   ↓
[Calcula comissão: tabela A/B/C/D, valida comprador]
   ↓
[Gera 2 Excel: coordenador + 156 vendedores]
   ↓
Pronto para pagamento
```

**Resultado:** 2 tipos de relatório Excel
- **Coordenador:** Visão completa (1800+ linhas)
- **Vendedor:** Apenas seus pedidos (20-30 linhas cada)

---

## 📋 Estrutura do Projeto

```
projeto/
├── main.py                    ← RODAR ISTO
├── config.py                  ← EDITAR MÊS (1 linha)
├── blacklist.txt              ← EDITAR (vendedores a ignorar)
├── vendedores_sp.txt          ← EDITAR (classificação filial)
├── vendedores_mg.txt          ← EDITAR (classificação filial)
├── .env                       ← EDITAR (credenciais OMIE)
│
├── models.py                  ← Estruturas Pedido, InfoCusto
├── clients.py                 ← Integração OMIE (cache bulk)
├── database.py                ← SQLite com TTL
├── services.py                ← Lógica (extração, comissões)
├── reports.py                 ← Geração Excel
├── utils.py                   ← Helpers
│
├── comissoes.db               ← Gerado (banco local)
└── gerar_comissoes.log        ← Gerado (log detalhado)
```

**Apenas 8 linhas de Python para se preocupar! Tudo centralizado em `config.py`.**

---

## 🔄 Os 5 Passos

```
┌──────────────────────────────────────────────────────────┐
│ PASSO 1: Extração OMIE                                  │
│ • Busca API OMIE (pedidos + NFs paginado)              │
│ • Cache bulk em 1 chamada (não N chamadas)             │
│ Saída: [Pedido] com dados brutos                       │
└──────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────┐
│ PASSO 2: Cálculo de Comissões                           │
│ • Descobre simuladores Excel em pasta rede             │
│ • Lê em paralelo (8 threads)                           │
│ • Aplica tabela: A=2%, B=1.3%, C=0.7%, D=0.5%        │
│ Saída: [Pedido] com comissões preenchidas             │
└──────────────────────────────────────────────────────────┘
         ↙                          ↘
    ┌───────────────────┐    ┌──────────────────┐
    │ PASSO 3           │    │ PASSO 4          │
    │ Relatório         │    │ Marcar Faturados │
    │ Coordenador       │    │ sem Simulador    │
    └───────────────────┘    └──────────────────┘
         ↓                          ↓
    [Excel Completo]      [obs: "Anexe simulador!"]
         ↓                          ↓
         └──────────────────┬───────┘
                           ↓
        ┌──────────────────────────────────┐
        │ PASSO 5: Distribuição Vendedores │
        │ • 1 Excel por vendedor           │
        │ • Apenas seus pedidos            │
        │ • Pasta por filial (SP/MG)       │
        └──────────────────────────────────┘
```

---

## 📊 Exemplo de Saída

### Log Console
```
════════════════════════════════════════════════
  GERADOR DE COMISSÕES  –  05/04/2026 10:30:15
════════════════════════════════════════════════
══ PASSO 1: extração OMIE ══
  OMIE → 142 vendedores carregados (cache OK)
  OMIE → 8548 clientes carregados (cache OK)
  Total: 1847 pedidos (1203 com NF + 644 sem NF)

══ PASSO 2: comissões ══
  Simuladores: 156 SP + 87 MG = 243 total
  Resumo: 156 OK | 67 pendentes | 20 ignorados | 0 erros

══ PASSO 3: relatório coordenador ══
  Salvo: 04_RELATORIO_GERAL_COMISSAO.xlsx (1847 linhas)

══ PASSO 4: marcando sem simulador ══
  67 pedido(s) faturado(s) sem simulador

══ PASSO 5: distribuição ══
  156 vendedores → 156 Excel gerados

✅ Concluído em 87.3 segundos
📄 Log: /projeto/gerar_comissoes.log
════════════════════════════════════════════════
```

### Excel Gerado
```
Data_Venda | Numero_Pedido | Cliente | Comissao_Vend_% | Comissao_Comp_% | Valor_Calculado | Obs
05/04/2026 | 001234        | EMP A   | 2.00%          | 2.00%          | 2,400.00        | Comissão Definida!
05/04/2026 | 001235        | EMP B   | 2.00%          | —              | 0.00            | Análise Compras Pendente
```

---

## ⚙️ Para Rodar

### Primeira Vez (5 min)
```bash
# 1. Renomear arquivo de env
mv _env .env

# 2. Editar credenciais OMIE
nano .env
# Preench: OMIE_APP_KEY, OMIE_APP_SECRET

# 3. Instalar dependências
pip install python-dotenv requests pandas openpyxl

# 4. Executar
python main.py
```

### Próximos Meses (2 min)
```bash
# 1. Editar data em config.py (1 linha):
# _MES = date(2026, 5, 1)  # novo mês

# 2. Rodar:
python main.py
```

### Se Travar
```bash
# Reset completo (apaga banco + cache)
rm comissoes.db
python main.py
```

---

## 🎯 Checklist Antes de Rodar

- [ ] Editar `config.py`: `_MES = date(2026, X, 1)` ← novo mês
- [ ] `.env` existe com credenciais OMIE válidas?
- [ ] Pastas de rede (Z:, Y:) acessíveis?
- [ ] `blacklist.txt` atualizada?
- [ ] `vendedores_sp.txt` + `vendedores_mg.txt` atualizadas?
- [ ] Ninguém com Excel aberto?
- [ ] Há espaço em disco (~10 MB)?

---

## 🔐 O Que Editar vs Não Editar

### ✅ EDITAR (Conforme necessário)
```
config.py                # Data do mês (1 linha!)
blacklist.txt            # Vendedores a ignorar
vendedores_sp.txt        # Classificação SP
vendedores_mg.txt        # Classificação MG
.env                     # Credenciais OMIE
```

### ❌ NÃO EDITAR (Código)
```
main.py                  # Orquestração (não mexer)
services.py              # Lógica de negócio (não mexer)
clients.py               # Integração OMIE (não mexer)
database.py              # Banco (não mexer)
reports.py               # Excel (não mexer)
models.py                # Estruturas (não mexer)
utils.py                 # Helpers (não mexer)
```

---

## 📈 Arquitetura em 30 Segundos

```
4 Camadas:

1. ENTRADA (clients.py)
   └─ HTTP → OMIE API

2. LÓGICA (services.py)
   └─ Extrai, calcula, valida

3. PERSISTÊNCIA (database.py)
   └─ SQLite com cache (TTL)

4. SAÍDA (reports.py)
   └─ Excel formatado
```

**Princípio:** Cada camada faz UMA coisa bem feita.

---

## ⚠️ Erro? Consulte Isto

| Erro | Solução |
|------|---------|
| `KeyError: OMIE_APP_KEY` | `mv _env .env` + editar credenciais |
| `PermissionError` Excel | Fechar Excel, reexecuta |
| 0 clientes carregados | Credenciais OMIE erradas (APP_KEY/SECRET) |
| Vendedor sem relatório | Adicionar a `vendedores_sp.txt` ou `vendedores_mg.txt` |
| `[SKIP] Pedido sem vendedor` | Normal — será ignorado |
| Banco lento | `rm comissoes.db && python main.py` |

**Mais detalhes:** Consulte `quick_reference.md` (seção Troubleshooting)

---

## 🎓 Conceitos Principais

### **Tabela de Comissão**
```
Letra  Comissão
  A    2.00%
  B    1.30%
  C    0.70%
  D    0.50%
```
Vem da célula **Z5** do Excel do vendedor.

### **Status "Prejuízo"**
Célula **AB12** do Excel. Se = "Prejuízo", comissão vira **0%**.

### **Comissão Definida vs Pendente**
- **DEFINIDA:** Comprador aprovou (tem arquivo "OK") → usa `min(vendedor, comprador)`
- **PENDENTE:** Comprador ainda não viu → comissão 0%, obs: "Análise Compras Pendente"

### **Cache com TTL**
- **Vendedores:** 1x em bulk OMIE, reutiliza por 24h
- **Clientes:** 1x em bulk OMIE, reutiliza por 7 dias
- **Vantagem:** Rápido (sem HTTP repetido)
- **Desvantagem:** Novo vendedor aparece no máximo amanhã

---

## 📚 Documentação Disponível

### Pelo Documento

**Não sabe por onde começar?** → Leia `indice_documentacao.md`

**Não-técnico?** → Leia `resumo_executivo.md`

**Entender arquitetura?** → Leia `projeto_comissoes_analise.md`

**Desenvolvedor?** → Leia `desenvolvimento_tecnico.md`

**Cola rápida?** → Leia `quick_reference.md`

### Impressão Recomendada

Imprima `quick_reference.md` e cole ao lado do monitor. 📌

---

## 🚀 Próximos Passos

1. **Escolha um documento** (conforme seu perfil)
2. **Leia em 20-60 minutos**
3. **Execute `python main.py`**
4. **Consulte `quick_reference.md`** quando tiver dúvida
5. **Está tudo funcionando?** ✅ Pronto!

---

## 📞 Suporte

**Logs detalhados:** `gerar_comissoes.log` (DEBUG + INFO)

**Banco:** `comissoes.db` (SQLite, inspecionável)

**Dúvidas comuns:** Consulte `quick_reference.md`

**Erros técnicos:** Consulte `projeto_comissoes_analise.md` (seção relevante)

---

## ✅ Validação

Você está pronto se conseguir responder:

- [ ] Qual é o objetivo do sistema?
- [ ] Quais são os 5 passos?
- [ ] Como rodar pela primeira vez?
- [ ] Quais arquivos editar mensalmente?
- [ ] Como interpretar um erro?

**Não consegue?** Volte ao documento certo! 📚

---

## 📊 Estatísticas do Projeto

| Métrica | Valor |
|---------|-------|
| Arquivos Python | 8 |
| Linhas de Código | ~1700 |
| Linhas de Documentação | ~2000 |
| Tempo execução típico | 80-120s |
| Pedidos/mês processados | ~1850 |
| Simuladores lidos/mês | ~240 |
| Excel gerados/mês | 157 (1 coord + 156 vend) |
| DB local | SQLite (< 10 MB) |

---

## 🎯 Conclusão

Este sistema é:
- ✅ **Automático:** Roda 1 comando, gera tudo
- ✅ **Resiliente:** Erros em 1 página não interrompem resto
- ✅ **Otimizado:** Cache bulk, leitura paralela
- ✅ **Bem documentado:** 5 documentos, 2000+ linhas
- ✅ **Fácil de manter:** 1 linha a editar por mês
- ✅ **Extensível:** Padrões claros para novas features

**Bom usar!** 🚀

---

**Versão:** 1.0 (Estável)  
**Data:** 05 de Abril de 2026  
**Documentação:** Completa ✅

---

## 📖 Índice de Documentação

1. **indice_documentacao.md** ← Guia de navegação
2. **resumo_executivo.md** ← Para não-técnico
3. **projeto_comissoes_analise.md** ← Para analista
4. **desenvolvimento_tecnico.md** ← Para desenvolvedor
5. **quick_reference.md** ← Cola (imprimir!)

👉 **Comece pelo índice ou pelo seu perfil acima!**

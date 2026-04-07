# 🛠️ Guia Técnico de Desenvolvimento — Sistema de Comissões

---

## 📐 Padrões de Arquitetura

### **Divisão de Responsabilidades**

```
config.py          → Centraliza TODAS as constantes
                     (datas, pastas, credenciais, tabelas)

models.py          → Estruturas de dados puros
                     (Pedido, InfoCusto)
                     Sem dependências externas (apenas stdlib)

clients.py         → Comunicação externa (OMIE, HTTP, cache)
                     I/O apenas — ZERO regra de negócio

database.py        → Persistência e leitura do banco
                     SQL direto via sqlite3
                     Cache com TTL

services.py        → Regra de negócio (lógica)
                     Orquestra clients + database + models
                     Lê arquivos Excel, valida blacklist

reports.py         → Geração de saídas (Excel)
                     Recebe DataFrames prontos
                     Sem acoplamento com services

utils.py           → Funções compartilhadas (normaliza nomes)
                     ZERO dependências internas

main.py            → Orquestração dos 5 passos
                     Sem lógica de negócio
                     Chamadas sequenciais bem documentadas
```

### **Camadas de Dados**

```
1. OMIE API
   ↓
2. OmieClient (cache bulk 1x)
   ↓
3. Lista bruta [dict]
   ↓
4. Pedido (models.Pedido)
   ↓
5. Database (upsert + cache)
   ↓
6. services.calcular_comissoes()
   ↓
7. pd.DataFrame
   ↓
8. reports → Excel
```

---

## 🔐 Padrões de Configuração

### **Config é a Fonte da Verdade**
```python
# ❌ ERRADO
valor = os.environ.get("PASTA_COORD", "C:\\default")

# ✅ CORRETO
from config import PASTA_COORD  # Já validado, normalizado, com fallback
```

### **Adicionar Nova Configuração**
```python
# config.py

# 1. Definir constante
NOVA_OPCAO = "valor_default"

# 2. Se vem de .env, sobrescrevê-la
NOVA_OPCAO = os.environ.get("NOVA_OPCAO", NOVA_OPCAO)

# 3. Importar em outro lugar
from config import NOVA_OPCAO
```

---

## 🗄️ Padrões de Banco de Dados

### **Operações CRUD**

```python
# ✅ Usar SEMPRE context manager
with _conectar() as conn:
    resultado = conn.execute(...)
    conn.execute("INSERT ...")
    # Auto-commit ao sair do bloco

# ❌ NUNCA deixar conexão aberta
conn = sqlite3.connect(...)
# ... operações ...
# esquecer conn.close()
```

### **Transações Seguras**
```python
# ✅ CORRETO - Isolamento completo
with _conectar() as conn:
    conn.executemany(...)  # múltiplas ops atômicas

# ✅ CORRETO - Detecta mudanças
row = conn.execute("SELECT ... WHERE id=?", (id,)).fetchone()
if row and _fingerprint(novo) != _fingerprint(row):
    conn.execute("UPDATE ...")  # só atualiza se mudou
```

### **Cache com TTL**
```python
# Pattern do projeto:
def _expirado_horas(iso_str: str, horas: int) -> bool:
    try:
        return datetime.now() - datetime.fromisoformat(iso_str) > timedelta(hours=horas)
    except Exception:
        return True  # Se parse falhar, considera expirado

# Uso:
def get_dados():
    cached = _conectar().execute("SELECT ... FROM cache").fetchone()
    if cached and not _expirado_horas(cached["atualizado_em"], 24):
        return cached
    # Senão, busca da API
    return buscar_api()
```

---

## 🌐 Padrões de Integração Externa (OMIE)

### **Retry Automático com Backoff**
```python
# clients.py já implementa isso:
retry = Retry(
    total=5,                                    # 5 tentativas
    backoff_factor=2.0,                        # 1s, 2s, 4s, 8s, 16s
    status_forcelist=(425, 429, 500, 502, 503, 504),  # quais erros retry
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
```

**Adicionar novo status para retry:**
```python
status_forcelist = (425, 429, 500, 502, 503, 504, 503, 418)  # adiciona 418
```

### **Tratamento de Páginas com Erro**
```python
# Pattern usado em listar_pedidos() e listar_nfs():

pagina = 1
tot_pag = 1
erros_pag: list[int] = []

while pagina <= tot_pag:
    try:
        data = self._post(URL, "ListarPedidos", {...})
        # processa
    except Exception as exc:
        log.warning("Falha página %d: %s — continuando.", pagina, exc)
        erros_pag.append(pagina)
    pagina += 1

if erros_pag:
    log.warning("%d página(s) com erro (%s) — resultado pode estar incompleto.", len(erros_pag), erros_pag)
```

---

## 📂 Padrões de Leitura de Arquivos

### **Leitura Otimizada de Excel (ZIP + XML)**
```python
# services.py usa este padrão:

def _ler_valor_celula(arquivo_zip: zipfile.ZipFile, caminho_xml: str, celula: str) -> str:
    """
    Lê valor de célula específica sem openpyxl (muito pesado).
    Usa ZIP internamente + XPath no XML.
    """
    try:
        xml_str = arquivo_zip.read(caminho_xml).decode('utf-8')
        root = ET.fromstring(xml_str)
        # Procura célula com referência (ex: "Z5")
        for cell in root.findall('.//{...}c[@r="Z5"]'):
            val_elem = cell.find('{...}v')
            if val_elem is not None:
                return val_elem.text or ""
    except Exception:
        pass
    return ""
```

**Vantagem:** 5-10x mais rápido que openpyxl para ler 2 células

### **Leitura Paralela**
```python
def _ler_simuladores_em_paralelo(sims: dict[str, Path]) -> dict[str, InfoCusto]:
    """ThreadPoolExecutor com isolamento de erro por arquivo."""
    infos = {}
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_ler_simulador, arq): nome
            for nome, arq in sims.items()
        }
        
        for future in as_completed(futures):
            nome = futures[future]
            try:
                info = future.result()
                infos[nome] = info
            except Exception as exc:
                log.error("Erro ao ler '%s': %s", nome, exc)
                # Continua com próximo — não falha a execução
    
    return infos
```

---

## 🎯 Padrões de Validação

### **Blacklist**
```python
# ✅ CORRETO - normaliza antes de comparar
def _na_blacklist(nome_vendedor: str, blacklist: set[str]) -> bool:
    return nome_para_pasta(nome_vendedor).upper() in blacklist

# Uso:
nome = "HUGO DOS SANTOS GONÇALVES"
chave = nome_para_pasta(nome).upper()  # "HUGO_DOS_SANTOS_GONÇALVES"
if chave in blacklist:
    # ignorar
```

### **Fingerprint para Detectar Mudanças**
```python
def _fingerprint(p: Pedido) -> str:
    """3 campos chave que, se mudarem, justificam UPDATE."""
    return f"{p.valor_pedido}|{p.valor_faturado}|{p.nota_fiscal}"

# Uso na detecção de atualização:
if row and _fingerprint(novo) != _fingerprint(antigo):
    # houve mudança — atualiza
else:
    # inalterado — pula
```

---

## 📊 Padrões de Logging

### **Níveis Corretos**
```python
# DEBUG — informações detalhadas (para troubleshooting)
log.debug("      Lendo simulador: %s", arquivo)

# INFO — eventos principais (início/fim de seção)
log.info("  Extraindo OMIE…")
log.info("  %d pedidos carregados.", len(pedidos))

# WARNING — coisa anômala mas não interrompe
log.warning("  Falha ao carregar página %d — continuando.", pagina)

# ERROR — falha em 1 item, resta continua
log.error("  Erro ao ler '%s': %s", nome, exc)

# CRITICAL — coisa tão ruim que aborta
log.critical("Falha crítica em comissões: %s — abortando.", exc)
sys.exit(1)
```

### **Estrutura de Seções**
```python
log.info("════════════════════════════════════════════════")
log.info("  GERADOR DE COMISSÕES  –  %s", datetime.now())
log.info("════════════════════════════════════════════════")

log.info("══ PASSO 1: extração OMIE ══")
# ...
log.info("  Total: %d pedidos", len(pedidos))

log.info("══ PASSO 2: comissões ══")
# ...
log.info("  Resumo: %d OK | %d pendentes", ok, pendentes)
```

---

## 🚀 Padrões de Tratamento de Erro

### **Falha Crítica (Aborta)**
```python
# services.py — calcular_comissoes():
try:
    # lógica
except Exception as exc:
    log.critical("Falha crítica no cálculo: %s", exc, exc_info=True)
    sys.exit(1)  # ← Parar execução
```

### **Falha Isolada (Continua)**
```python
# clients.py — listar_pedidos():
while pagina <= tot_pag:
    try:
        # lógica
    except Exception as exc:
        log.warning("Falha página %d: %s — continuando.", pagina, exc)
        erros_pag.append(pagina)
    pagina += 1

if erros_pag:
    log.warning("Resultado pode estar incompleto: %s", erros_pag)
```

### **Fallback Automático**
```python
# reports.py — _escrever_excel():
try:
    shutil.copy2(tmp_path, caminho)  # cópia normal
except PermissionError:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = caminho.with_stem(f"{caminho.stem}_{ts}")
    shutil.copy2(tmp_path, fallback)
    log.warning("Arquivo bloqueado — salvo como: %s", fallback)
```

---

## 🧪 Padrões para Testes

### **Estrutura para Testes Unitários**
```python
# test_services.py (sugestão)

import unittest
from unittest.mock import patch, MagicMock
from services import _normalizar_data, _na_blacklist, _pedido_excluido

class TestNormalizacao(unittest.TestCase):
    def test_normalizar_data_iso(self):
        resultado = _normalizar_data("2026-04-05")
        self.assertEqual(resultado, "05/04/2026")
    
    def test_normalizar_data_vazia(self):
        self.assertEqual(_normalizar_data(None), "-")
        self.assertEqual(_normalizar_data(""), "-")
```

### **Mock de OmieClient**
```python
@patch('clients.OmieClient')
def test_extrair_omie(mock_omie):
    mock_omie.return_value.listar_pedidos.return_value = [
        {
            "cabecalho": {"codigo_pedido": 1, "numero_pedido": "000001"},
            ...
        }
    ]
    pedidos = extrair_omie()
    assert len(pedidos) > 0
```

---

## 🔄 Extensões Recomendadas

### **1. Auditoria (Quem mudou o quê, quando?)**
```python
# database.py — adicionar tabela:
"""
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    numero_pedido TEXT,
    campo TEXT,
    valor_antigo TEXT,
    valor_novo TEXT,
    data_mudanca TEXT,
    usuario TEXT
)
"""

# services.py — ao atualizar:
def atualizar_com_auditoria(pedido, usuario="sistema"):
    if _fingerprint_mudou(pedido):
        database.registrar_auditoria(pedido.numero_pedido, usuario)
        database.atualizar_comissoes([pedido])
```

### **2. Alertas para Vendedor**
```python
# services.py — novo módulo:
def enviar_alerta_vendedor(nome_vendedor, pedido):
    email = carregar_email(nome_vendedor)
    assunto = f"Pedido {pedido.numero_pedido} — comissão pendente"
    corpo = f"""
    Seu pedido ainda aguarda análise do comprador.
    Verifique: {pedido.obs_comissao}
    """
    enviar_email(email, assunto, corpo)

# main.py — após passo 2:
for p in pedidos:
    if "Pendente" in p.obs_comissao:
        enviar_alerta_vendedor(p.nome_vendedor, p)
```

### **3. Comparativo Mês a Mês**
```python
# reports.py — nova função:
def gerar_comparativo_mensal(mes_atual, mes_anterior):
    df_atual = carregar_mes(mes_atual)
    df_prev = carregar_mes(mes_anterior)
    
    df_comp = pd.merge(
        df_atual[["numero_pedido", "valor_comissao"]],
        df_prev[["numero_pedido", "valor_comissao"]],
        on="numero_pedido",
        suffixes=("_atual", "_anterior")
    )
    df_comp["variacao_%"] = (
        (df_comp["valor_comissao_atual"] - df_comp["valor_comissao_anterior"]) 
        / df_comp["valor_comissao_anterior"] * 100
    )
    return df_comp
```

### **4. API REST para Consulta**
```python
# api.py (novo arquivo)
from flask import Flask, jsonify
import database

app = Flask(__name__)

@app.route("/vendedor/<nome>/mes/<mes>")
def comissoes_vendedor(nome, mes):
    pedidos = database.carregar_pedidos_vendedor(nome, mes)
    return jsonify([p.to_dict() for p in pedidos])

@app.route("/resumo/<mes>")
def resumo_mes(mes):
    df = carregar_mes(mes)
    return jsonify({
        "total_pedidos": len(df),
        "valor_total": df["valor_comissao"].sum(),
        "por_filial": df.groupby("filial")["valor_comissao"].sum().to_dict(),
    })

if __name__ == "__main__":
    app.run(debug=False, port=5000)
```

---

## 🎓 Checklist para Nova Feature

Antes de commitar uma nova funcionalidade:

- [ ] **Logging:** Adicionei log.info, log.debug, log.warning em pontos estratégicos?
- [ ] **Teste Manual:** Rodei main.py completo e verifiquei output/log?
- [ ] **Edge Cases:** E se lista vazia? E se arquivo não existir? E se rede cair?
- [ ] **Config:** Todas as constantes em config.py?
- [ ] **Banco:** Se toca em database.py, testei com `rm comissoes.db` depois?
- [ ] **Imports:** Removidas importações não usadas? Verificado `import *`?
- [ ] **Type Hints:** Adicionados tipos em assinaturas (ex: `def foo(...) -> list[dict]`)?
- [ ] **Docstring:** Documentada a função/seção?
- [ ] **Performance:** Se lê arquivo grande, usei threading?
- [ ] **Retry:** Se faz HTTP, tem retry automático?

---

## 📝 Exemplo: Adicionar Nova Configuração de Comissão

**Cenário:** Criar comissão especial para clientes VIP

### Passo 1: Estender Config
```python
# config.py
CLIENTES_VIP = {"CLIENTE_A", "CLIENTE_B"}
COMISSAO_VIP_MULTIPLIER = 1.5  # 50% de aumento

TABELA_COMISSAO_VIP: dict[str, float] = {
    "A": 0.020 * COMISSAO_VIP_MULTIPLIER,  # 3%
    "B": 0.013 * COMISSAO_VIP_MULTIPLIER,  # 1.95%
    ...
}
```

### Passo 2: Estender Models
```python
# models.py
@dataclass
class Pedido:
    # ... campos existentes ...
    eh_cliente_vip: bool = False
```

### Passo 3: Estender Services
```python
# services.py
def _eh_cliente_vip(nome_cliente: str) -> bool:
    return nome_cliente.upper() in config.CLIENTES_VIP

# Em calcular_comissoes():
for linha in linhas_pedido:
    eh_vip = _eh_cliente_vip(linha.nome_cliente)
    tabela = config.TABELA_COMISSAO_VIP if eh_vip else config.TABELA_COMISSAO
    letra_v = str(info_vendor.letra_com or "").strip().upper()
    pct_v = tabela.get(letra_v, 0.0)
    linha.eh_cliente_vip = eh_vip
```

### Passo 4: Estender Reports
```python
# reports.py
COLUNAS_COORD.insert(4, "Cliente_VIP")  # depois de Nome_Cliente
# Excel já renderiza automaticamente
```

---

## 🎯 Conclusão

O projeto segue padrões claros de separação de responsabilidades:
- **Config:** Source of truth
- **Models:** Puro dados
- **Clients:** I/O apenas
- **Database:** Persistência
- **Services:** Regra de negócio
- **Reports:** Saídas
- **Main:** Orquestração

Manter essa disciplina facilita manutenção, testes e extensões futuras! 🚀

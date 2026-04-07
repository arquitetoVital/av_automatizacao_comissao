"""
utils.py
────────
Funções utilitárias compartilhadas entre módulos.
Sem dependências internas de negócio — apenas stdlib.
"""


def nome_para_pasta(nome: str) -> str:
    """
    Converte nome de vendedor para formato de pasta de rede.
    'HUGO GONÇALVES' → 'HUGO_GONÇALVES'
    """
    limpo = "".join(c for c in str(nome) if c not in r'\/:*?"<>|').strip()
    return limpo.replace(" ", "_")

"""
utils.py
────────
Funções utilitárias compartilhadas entre módulos.
Sem dependências internas de negócio — apenas stdlib.
"""

import unicodedata


def nome_para_pasta(nome: str) -> str:
    """
    Converte nome de vendedor para formato de pasta de rede.
    'HUGO GONÇALVES' → 'HUGO_GONÇALVES'
    """
    limpo = "".join(c for c in str(nome) if c not in r'\/:*?"<>|').strip()
    return limpo.replace(" ", "_")


def normalizar_str(s: str) -> str:
    """Remove acentos, coloca maiúsculo e strip — usado para comparar nomes."""
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()

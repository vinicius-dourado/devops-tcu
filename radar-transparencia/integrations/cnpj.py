"""Utilitários para validação e formatação de CNPJs brasileiros."""

from __future__ import annotations

import re


def _clean_cnpj(cnpj: str) -> str:
    """Remove caracteres não numéricos de um CNPJ."""
    return re.sub(r"\D", "", cnpj)


def validate_cnpj(cnpj: str) -> bool:
    """Valida um CNPJ verificando os dígitos verificadores.

    Args:
        cnpj: CNPJ em qualquer formato (com ou sem pontuação).

    Returns:
        True se o CNPJ é válido, False caso contrário.
    """
    cnpj = _clean_cnpj(cnpj)

    if len(cnpj) != 14:
        return False

    # CNPJs com todos os dígitos iguais são inválidos
    if len(set(cnpj)) == 1:
        return False

    # Calcula primeiro dígito verificador
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(cnpj[i]) * weights1[i] for i in range(12))
    remainder = total % 11
    digit1 = 0 if remainder < 2 else 11 - remainder

    if int(cnpj[12]) != digit1:
        return False

    # Calcula segundo dígito verificador
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(cnpj[i]) * weights2[i] for i in range(13))
    remainder = total % 11
    digit2 = 0 if remainder < 2 else 11 - remainder

    return int(cnpj[13]) == digit2


def format_cnpj(cnpj: str) -> str:
    """Formata um CNPJ no padrão XX.XXX.XXX/XXXX-XX.

    Args:
        cnpj: CNPJ com ou sem pontuação (14 dígitos numéricos).

    Returns:
        CNPJ formatado, ou a string original se não tiver 14 dígitos.
    """
    digits = _clean_cnpj(cnpj)
    if len(digits) != 14:
        return cnpj
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def clean_cnpj(cnpj: str) -> str:
    """Remove toda pontuação de um CNPJ, retornando apenas os 14 dígitos.

    Args:
        cnpj: CNPJ com ou sem pontuação.

    Returns:
        String com apenas os dígitos numéricos.
    """
    return _clean_cnpj(cnpj)

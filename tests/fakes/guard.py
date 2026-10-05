"""Reexporta o intertravamento de produção, que agora mora em ``rumbapy._worker.guard`` (movido com ``git mv``).

Mantido para os testes e ferramentas que importam ``tests.fakes.guard``: todos os nomes são os MESMOS objetos do
módulo do pacote (inclusive os auxiliares privados).
"""
from rumbapy._worker.guard import (  # noqa: F401
    AID_BY_A_CODE,
    AID_BY_CODE,
    ALWAYS_BLOCKED,
    TEXT_FUNCS,
    GuardedDll,
    GuardPolicy,
    GuardViolation,
    _GuardedFunction,
    _keys_text,
    classify_keys,
)

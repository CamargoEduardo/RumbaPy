"""Módulo removido na v2 (B.9.3): a lógica da v0.1.5 (pywin32/pywinauto/IE COM) não existe mais.

Use ``rumbapy.open("D")`` (API nova) ou ``rumbapy.RumbaClient("D")`` (compatível com a v0.1.5).
"""
# ruff: noqa: I001

raise ImportError("rumbapy.api foi removido na v2; use rumbapy.open(...) ou RumbaClient")

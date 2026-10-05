"""pip falso (``RUMBAPY_TEST_PIP``): o bootstrap roda ``[python, fake_pip.py, *argv_do_pip[3:]]``.

Variáveis:
* ``FAKE_PIP_LOG``: arquivo onde cada execução acrescenta ``json.dumps(sys.argv[1:])`` numa linha;
* ``FAKE_PIP_RC``: código de saída (padrão 0);
* ``FAKE_PIP_MODE``: ``install`` (padrão: copia ``FAKE_PIP_SOURCE`` para ``FAKE_PIP_SITE/rumbapy`` e grava o
  ``dist-info`` com o ``direct_url.json`` da URL recebida) ou ``noop`` (não muda nada);
* ``FAKE_PIP_SLEEP``: segundos de espera antes de "instalar".
Só usa a stdlib e nunca acessa a rede.
"""
# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import shutil
import json
import time
import sys
import os


def main(argv: list[str]) -> int:
    log = os.environ.get("FAKE_PIP_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")
    time.sleep(float(os.environ.get("FAKE_PIP_SLEEP", "0") or 0))
    rc = int(os.environ.get("FAKE_PIP_RC", "0") or 0)
    if rc != 0:
        print("ERROR: falha simulada", file=sys.stderr)
        return rc
    if os.environ.get("FAKE_PIP_MODE", "install") != "install":
        return 0
    url = next(a for a in reversed(argv) if a.endswith(".zip"))
    site = Path(os.environ["FAKE_PIP_SITE"])
    source = Path(os.environ["FAKE_PIP_SOURCE"])
    target = site / "rumbapy"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for old in site.glob("rumbapy-*.dist-info"):
        shutil.rmtree(old)
    dist = site / "rumbapy-2.0.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: rumbapy\nVersion: 2.0.0\n", encoding="utf-8")
    (dist / "RECORD").write_text("", encoding="utf-8")
    (dist / "direct_url.json").write_text(json.dumps({"url": url, "archive_info": {}}), encoding="utf-8")
    print(f"Successfully installed rumbapy-2.0.0 (falso, {url})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

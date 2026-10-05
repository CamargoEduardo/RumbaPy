"""Versão do RumbaPy e identidade do repositório (fonte única; o backend de build lê ``__version__`` por regex)."""

__version__ = "2.0.0"
PROTOCOL = 2

REPO_OWNER = "CamargoEduardo"
REPO_NAME = "RumbaPy"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
MASTER_REF = "refs/heads/master"

# Derivados (mesmos valores que _bootstrap usa; ele não importa rumbapy.* e repete as constantes, H.6)
GIT_URL = f"{REPO_URL}.git"
MASTER_ARCHIVE_URL = f"{REPO_URL}/archive/{MASTER_REF}.zip"
RECOVERY_COMMAND = f"python -m pip install --force-reinstall --no-deps {MASTER_ARCHIVE_URL}"


def archive_url(sha: str) -> str:
    """URL do zip de um commit exato (formato usado pelo update, H.4)."""
    return f"{REPO_URL}/archive/{sha}.zip"

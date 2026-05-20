# RumbaPy

Biblioteca Python para automação de terminais IBM/RUMBA via EHLAPI.

Author: Eduardo Camargo da Silva  
GitHub: https://github.com/littleplankton

O projeto fornece uma interface de alto nível para automação de sistemas legados acessados por terminais RUMBA, utilizando comunicação entre processos Python 64-bit e Python 32-bit.

---

# Arquitetura

A DLL `ehlapi32.dll` utilizada pelo RUMBA é exclusivamente 32-bit.

Por esse motivo, o projeto utiliza uma arquitetura híbrida:

```text
Python 64-bit
  ↓
RumbaClient
  ↓ socket/subprocess
Servidor Python 32-bit
  ↓
ehlapi32.dll
  ↓
RUMBA
```

- O processo principal roda em Python 64-bit;
- O acesso à DLL ocorre em um subprocesso Python 32-bit;
- A comunicação entre cliente e servidor ocorre via socket TCP local.

Isso permite utilizar bibliotecas modernas do ecossistema Python 64-bit (`pandas`, `numpy`, `polars`, `openpyxl`, etc.) sem abrir mão da integração com o RUMBA.

---

# Estrutura do Projeto

```text
RumbaPy/
├─ src/
│  └─ rumbapy/
│     ├─ __init__.py
│     ├─ client.py
│     ├─ server.py
│     ├─ api.py
│     └─ config.py
│
├─ pyproject.toml
├─ config.example.ini
└─ README.md
```

---

# Requisitos

- Windows
- RUMBA instalado
- Python 64-bit
- Python 32-bit
- `ehlapi32.dll`

---

# Instalação

## Clonar o repositório

```bash
git clone https://github.com/littleplankton/RumbaPy.git
cd RumbaPy
```

---

## Instalar ambiente principal (64-bit)

```bash
pip install -e .
```

---

## Instalar ambiente 32-bit

```bash
py -3.13-32 -m pip install -e .
py -3.13-32 -m pip install pywin32 pywinauto
```

---

# Configuração

Crie um arquivo `config.ini` baseado em `config.example.ini`.

Exemplo:

```ini
[cics]
id = your_id
password = your_password

[rhelp]
id = your_id
password = your_password

[32bit_python]
path = C:/Users/seu_id/AppData/Local/Programs/Python/Python313-32/python.exe

[rumba]
dll_path = C:/Program Files (x86)/Micro Focus/RUMBA/system/ehlapi32.Dll
```

---

# Uso Básico

```python
from rumbapy import RumbaClient

cics = RumbaClient(
    terminal_type='D',
    config_path='config.ini'
)

cics.logon_cics()
```

---

# Exemplo

```python
from rumbapy import RumbaClient

cics = RumbaClient('D', 'config.ini')

cics.logon_cics()

cics.copy_string_to_ps(
    y=3,
    x=13,
    text='ABC123'
)

cics.send_key('ENTER')

```

---

# Terminais Suportados

| Terminal | Uso |
|---|---|
| A | RHELP |
| D | CICS |
| Z | CICS |

---

# Logging

Exemplo:

```python
import logging
from pathlib import Path

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    encoding="utf-8",
)
```

---

# Uso como Dependência

O pacote pode ser utilizado diretamente em outros projetos:

```txt
git+https://github.com/littleplankton/RumbaPy.git@v0.1.0
```

Exemplo no `requirements.txt`:

```txt
git+https://github.com/littleplankton/RumbaPy.git@v0.1.0
pandas
openpyxl
polars
```

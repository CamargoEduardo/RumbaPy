# Arquitetura do RumbaPy (estado atual — v0.1.5, commit `5c03d47`)

> Documento de referência para humanos e agentes. Descreve **como o projeto funciona hoje** e
> **por que ele é assim**. Para o funcionamento interno do RUMBA/EHLLAPI veja
> [`docs/RUMBA_EHLLAPI.md`](docs/RUMBA_EHLLAPI.md) e [`docs/RUMBA_AMBIENTE.md`](docs/RUMBA_AMBIENTE.md).
> Para problemas conhecidos veja [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) e para a evolução
> planejada veja [`docs/PLANO_DE_MELHORIAS.md`](docs/PLANO_DE_MELHORIAS.md).

---

## 1. Propósito

Automatizar, a partir de Python, sessões de terminal **3270 (mainframe IBM)** abertas pelo
**Micro Focus RUMBA Web-to-Host Pro 6.9.4** da Volvo (`https://rumba.srv.volvo.com/rumba694/...`),
principalmente:

| Short name | Perfil W2H | Sistema | Host TN3270 | Tela |
|---|---|---|---|---|
| `D` | `BRAZIL_D` | CICS Brasil | host CICS-BR | 24×80 (modelo 2) |
| `Z` | `BRAZIL_Z` | CICS Brasil (2ª sessão) | host CICS-BR | 24×80 (modelo 2) |
| `A` | `IMS_A` | IMS / RHELP (C1) | host IMS-SE | **32×80 (modelo 3)** |

> Endereços reais dos hosts ficam em `docs/interno/AMBIENTE_INTERNO.md` (não versionado — o repositório é público).

O usuário final é um analista que escreve scripts Python (64-bit, com pandas etc.) que leem e
escrevem em telas do CICS/IMS.

---

## 2. Visão geral em camadas

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Script do usuário  (Python 64-bit: pandas, numpy, openpyxl...)              │
│   from rumbapy import RumbaClient                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ client.py  RumbaClient            (roda no Python 64-bit)                    │
│   • auto-update obrigatório (git ls-remote + pip) dos dois Pythons           │
│   • descobre o Python 32-bit, sobe o servidor se não houver                  │
│   • 1 conexão TCP por comando, JSON, localhost:8500/8600/8700/8800           │
├───────────────────────────── TCP JSON (localhost) ───────────────────────────┤
│ server.py  servidor persistente   (roda no Python 32-bit, processo separado) │
│   • thread por conexão; dict `sessions[session_id] -> RumbaAPI`              │
│   • fluxos de alto nível: logon_cics, logon_rhelp, screen_load               │
├──────────────────────────────────────────────────────────────────────────────┤
│ api.py  RumbaAPI                  (Python 32-bit)                            │
│   • ctypes -> ehlapi32.Dll (funções WD_*)                                    │
│   • abre o terminal automatizando o Internet Explorer (COM)                  │
│   • fecha o terminal com pywinauto (UIA) por título de janela                │
├───────────────────────── ctypes (stdcall, 32-bit) ───────────────────────────┤
│ C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll                 │
│   • seção .SHARDAT compartilhada entre processos (tabela de sessões A..Z)    │
│   • IPC: memória "HLLAPIMem<id>" + PostMessage("WDHLLAPI SEND") + evento     │
├──────────────────────────── IPC Win32 (mesma máquina) ───────────────────────┤
│ Processo do IE (iexplore.exe 32-bit) com a página profpro/BRAZIL_D.html      │
│   ProContainer.ocx  ("NetManage.ProControlContainer.1")                      │
│     ├─ WdMfDsp.ocx   (ObjectX Mainframe Display — o emulador 3270)           │
│     └─ WDApiSvc.ocx  (ObjectX HLLAPI Control — servidor HLLAPI/DDE)          │
├──────────────────────────────── TN3270 / TCP 23 ─────────────────────────────┤
│ Mainframe IBM z/OS (CICS / IMS)                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Por que a arquitetura é assim (restrições que a moldaram)

1. **A DLL é 32-bit e não existe versão 64-bit.** Todo o RUMBA instalado é x86 (inclusive os
   assemblies .NET). Um processo 64-bit não consegue carregar `ehlapi32.Dll`. Daí o **Python 32-bit**
   obrigatório para quem fala com a DLL.
2. **O usuário quer o ecossistema 64-bit** (pandas/numpy/pyarrow têm wheels 64-bit, memória).
   Daí a separação **cliente 64-bit ↔ servidor 32-bit**.
3. **A conexão HLLAPI é por processo** (ver `docs/RUMBA_EHLLAPI.md` §4) e precisa sobreviver entre
   chamadas → o lado 32-bit é um **servidor persistente**, não um subprocesso por chamada.
4. **A sessão HLLAPI só existe se o terminal foi aberto por uma página `_api`.** O W2H só carrega
   o controle HLLAPI (`WDApiSvc.ocx`) e só registra os short names (`D`, `Z`, `A`) quando o perfil
   tem `HLLAPIShortName != 0` — os perfis linkados por `index_pro_api.html` (o atalho padrão do
   usuário, `RumbaWebToHost.vbs`, abre `index_pro.html`, **sem** HLLAPI). Por isso o código **abre o
   terminal sozinho** automatizando o IE: navega até `index_pro_api.html` e clica no link
   `BRAZIL D` / `BRAZIL Z` / `IMS A`, que abre o popup `profpro/<PERFIL>.html` com o ActiveX.
5. **ActiveX só roda no motor do IE.** Daí `win32com` + CLSID `{D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E}`
   (`InternetExplorerMedium`, que roda em integridade média e evita prompts de modo protegido).
6. **Credenciais saíram do repositório.** Inicialmente lidas de `config.ini`; no commit `a2133f1`
   passaram a ser parâmetros (`logon_cics(uid, pwd)`), e o arquivo virou só exemplo.
7. **Distribuição sem PyPI.** O pacote é instalado de `git+https://github.com/CamargoEduardo/RumbaPy.git`
   nos **dois** Pythons. Como o código precisa ser idêntico nos dois lados, foi criado o
   **auto-update obrigatório** (requisito de negócio: o usuário deve sempre rodar a última versão).

---

## 4. Componentes

### 4.1 `src/rumbapy/__init__.py`
Exporta apenas `RumbaClient`.

### 4.2 `src/rumbapy/client.py` — lado 64-bit
| Elemento | Papel |
|---|---|
| `Mnemonic` | `Literal` com os nomes de teclas aceitos (espelha `api.KEYS`). |
| `RUMBAPY_REPO` | URL do repositório usado no auto-update. |
| `find_python_32bit()` | Varre `~/AppData/Local/Programs/Python/Python*/python.exe`, executa cada um e escolhe o **maior 32-bit ≥ 3.13**. |
| `update_python_32bit(py32)` | Executa um script no Python 32-bit que lê `importlib.metadata` (pywin32, pywinauto, rumbapy + `direct_url.json` → commit). Instala o que falta; compara o commit instalado com `git ls-remote <repo> HEAD`; reinstala (`--force-reinstall --no-deps`) se diferente. |
| `ensure_python_32bit()` | Guarda global: roda `update_python_32bit` **uma vez por processo**. |
| `update_python_64bit()` | Mesma lógica para o interpretador atual (`sys.executable`). **Roda a cada `RumbaClient()`** (sem guarda). |
| `RumbaClient.__init__(terminal_type, python32bit_path=None, auto_update=True)` | Resolve o Python 32-bit, roda o auto-update, gera `session_id = f"{terminal_type}_{uuid4().hex}"`, escolhe a porta (`D=8500, A=8600, Z=8700, outros=8800`) e chama `_start_server()`. |
| `_start_server()` | `ping`; se recusado (`WinError 10061`), sobe `python32 -m rumbapy.server --terminal_type=X` (`CREATE_NEW_PROCESS_GROUP`, stdout/stderr → DEVNULL) e espera até 30 s pelo `ping`. |
| `_send_command(action, params)` | Abre **um socket novo por comando**, envia JSON, `shutdown(SHUT_WR)`, lê até EOF, levanta `RuntimeError` se `success` for falso. |
| Métodos públicos | `logon_cics`, `logon_rhelp`, `send_key`, `copy_ps_to_string`, `copy_string_to_ps`, `copy_field`, `screen_load`, `close_terminal`, `disconnect`, `connect` — todos são *proxies* 1:1 para ações do servidor. |

### 4.3 `src/rumbapy/server.py` — lado 32-bit (processo persistente)
| Elemento | Papel |
|---|---|
| `sessions: dict[str, RumbaAPI]` | Uma `RumbaAPI` por `session_id` de cliente (nunca removida, exceto em `disconnect`). |
| `start_server()` | `argparse --terminal_type {D,A,Z}`; `bind(('localhost', porta))`; `accept()` → `threading.Thread(handle_client)`. |
| `handle_client()` | Lê até EOF, `json.loads`, chama `execute_command`, responde JSON, fecha. |
| `execute_command()` | `ping` (sem sessão) e despacho por `if/elif`: `open_cics`, `open_rhelp`, `logon_cics`, `logon_rhelp`, `send_key`, `copy_ps_to_string`, `copy_string_to_ps`, `copy_field`, `screen_load`, `close_terminal`, `disconnect`, `connect`. |
| `screen_load()` | *Polling* 1×/s por até 120 s comparando `copy_ps_to_string(y, x, len(text))` com `text` (ou diferente, se `text_diff`). A cada 5 s verifica a tela de "E R R O   G R A V E" em (5,31). |
| `logon_cics()` | Fecha terminal existente → abre `BRAZIL D/Z` → `WD_ConnectPS` (até 60 tentativas, 1/s) → espera `CICS` em (14,33) → `C` em (22,56) + ENTER → espera `TERMINAL` em (4,6) → uid em (12,21), senha em (13,21), ENTER → checa `SENHA EXPIRADA`/`SENHA INVALIDA` em (19,19) → espera `Signon` em (5,20). |
| `logon_rhelp()` | Fecha → abre `IMS A` → `WD_ConnectPS` (1 tentativa) → espera `USS010` (2,1) → `IMSRS` (5,1) + ENTER → espera `Welcome` (4,44) → uid (10,47), senha (11,47), ENTER → espera `Start` (16,20) → `RHELP` (16,47) + ENTER. |

### 4.4 `src/rumbapy/api.py` — lado 32-bit (acesso à DLL e ao IE)
| Elemento | Papel |
|---|---|
| `ERROR_CODES` | Descrição textual dos códigos de retorno por função (IBM EHLLAPI). |
| `KEYS` | Nome → mnemônico EHLLAPI (`ENTER=@E`, `F1..F9=@1..@9`, `F10=@a`, `F11=@b`, `F12=@c`, `PAGEUP=@u`, `PAGEDOWN=@v`, ...). Strings não mapeadas são enviadas cruas (permite digitar texto via `send_key`). |
| `RumbaConfig` | Caminho da DLL, `base_url` (`http://.../index_pro_api.html`), tamanho da janela (925×700), `timeout=120`. |
| `timeout(seconds)` | Decorator que executa a função numa *thread daemon* e levanta `TimeoutError` se passar do limite (a thread **continua rodando**). |
| `RumbaAPI.__init__(gs_short_name_ims)` | Carrega a DLL (`ctypes.windll.LoadLibrary`, exige `platform.architecture()=='32bit'`), `rumba_hwnd = 1`. |
| `navigate_to_terminal(label)` | `CoInitialize` → `Dispatch(InternetExplorerMedium)` invisível → `Navigate(base_url)` → espera `ReadyState==4` → clica no `<a>` cujo `innerText == label` → guarda PID do IE → `IE.Quit()` → procura em `Shell.Application.Windows()` uma janela do **mesmo PID** (o popup do perfil) → guarda seu HWND em `self.rumba_hwnd` → espera carregar → redimensiona. |
| `open_cics()` / `open_rhelp()` | `BRAZIL D`/`BRAZIL Z` (só `D`/`Z`) ou `IMS A`. |
| `close_terminal()` | `WD_DisconnectPS` + fecha via UIA **todas** as janelas cujo título casa `.*BRAZIL_D.*` / `.*BRAZIL_Z.*` / `.*IMS_A.*`. |
| `wd_*` | Wrappers ctypes de `WD_ConnectPS`, `WD_DisconnectPS`, `WD_CopyPSToString`, `WD_CopyStringToPS`, `WD_QuerySessionStatus` (bugado, nunca usado), `WD_CopyFieldToString`, `WD_Wait`, `WD_SendKey`. O 1º argumento é `self.rumba_hwnd`. |
| `copy_string_to_ps(y, x, s)` / `copy_ps_to_string(y, x, n)` / `copy_field(y, x)` | Convertem (linha, coluna) 1-based em posição linear `pos = (y-1)*80 + x` e chamam `WD_Wait` depois. |

---

## 5. Fluxos principais

### 5.1 `RumbaClient('D')`
```
RumbaClient('D')
 ├─ find_python_32bit()                      # se caminho não informado
 ├─ ensure_python_32bit(py32)                # 1x por processo
 │    ├─ py32 -c <script metadata>           # versões/commit instalados
 │    ├─ pip install pywin32 pywinauto       # se faltarem
 │    ├─ git ls-remote <repo> HEAD           # commit remoto
 │    └─ pip install --force-reinstall --no-deps git+<repo>   # se commit difere
 ├─ update_python_64bit()                    # TODA instanciação
 │    ├─ importlib.metadata -> direct_url.json
 │    ├─ git ls-remote <repo> HEAD
 │    └─ pip install [--force-reinstall --no-deps] git+<repo>
 └─ _start_server()
      ├─ ping localhost:8500 ── ok? usa o servidor existente (mesmo que de versão antiga!)
      └─ senão: Popen(py32 -m rumbapy.server --terminal_type=D) e espera ping (≤30 s)
```

### 5.2 Um comando qualquer
```
cliente.copy_ps_to_string(23, 7, 74)
  └─ TCP connect → {"session_id","action":"copy_ps_to_string","params":{"y":23,"x":7,"length":74,"terminal_type":"D"}}
       servidor: sessions[sid] (cria RumbaAPI se não existir) → RumbaAPI.copy_ps_to_string
         └─ thread(@timeout) → WD_CopyPSToString(hwnd, pos=1767, buf, 74) → WD_Wait(hwnd)
              └─ ehlapi32: mutex "PID<pid>" → memória HLLAPIMem<id> → PostMessage("WDHLLAPI SEND")
                   → WDApiSvc.ocx no processo do IE responde → evento sinalizado
  ← {"success": true, "text": "..."}
```

### 5.3 Abertura do terminal (dentro de `logon_*`)
```
close_terminal() → navigate_to_terminal('BRAZIL D')
  IE(oculto) → index_pro_api.html → click <a>BRAZIL D</a>
    → window.open('profpro/BRAZIL_D.html')   (popup no mesmo processo do IE)
       → procontainerfunc.js: cria ProContainer.ocx, CreateSession(1), configura Telnet
         (host CICS-BR:23), HLLAPI (ShortName=4 → 'D', EHLAPI32.DLL), Connect()
       → WDApiSvc.ocx chama ehlapi32!APIRegisterApp(hwnd, 'D', fmt)   # publica a sessão
  → WD_ConnectPS(hwnd, "D") até rc == 0
```

---

## 6. Protocolo cliente ↔ servidor

* Transporte: TCP em `localhost`, **uma conexão por requisição** (o cliente faz `shutdown(SHUT_WR)`
  depois de enviar; o servidor lê até EOF e fecha depois de responder).
* Portas fixas por short name: `D=8500`, `A=8600`, `Z=8700`, qualquer outro `8800`
  (mas o servidor só aceita `D`, `A`, `Z` no `argparse`).
* Requisição: `{"session_id": str, "action": str, "params": {..., "terminal_type": str}}`.
* Resposta: `{"success": bool, "error"?: str, "text"?: str, "result"?: any, "message"?: str}`.
* Sem autenticação, sem versionamento do protocolo, sem *keep-alive*.

---

## 7. Coordenadas

* A API pública usa **(y, x) = (linha, coluna), ambos 1-based**, como o RUMBA mostra na barra de status.
* Posição linear EHLLAPI (1-based): `pos = (y - 1) * 80 + x`.
* **Assume 80 colunas.** Funciona para modelo 2 (24×80) e modelo 3 (32×80, IMS A), mas não para
  modelos 4/5 (43×80 / 27×132).

---

## 8. Evolução (interpretação do `git log`)

| Data | Commit | Mudança | Motivo provável |
|---|---|---|---|
| 2026-05-05 | `691c8c2` | Primeiro commit: `app/src/rumba_api.py`, `rumba_server.py`, `rumba_client.py`, `config.ini` com credenciais, `run.py` de exemplo e **instaladores do Python commitados** | Projeto pessoal migrado para Git |
| 2026-05-05 | `b65ede5` | Remove os `.exe` do Python | Repositório pesado |
| 2026-05-20 | `32a6bf5` | Vira biblioteca instalável (`src/rumbapy`, `pyproject.toml`) | Reuso por outras pessoas/projetos |
| 2026-05-20 | `47b16b9` | Remove `config.ini` do Git | Segurança (credenciais) |
| 2026-07-23 | `b90b907` | `config.py` com caminho fixo | Tentativa de config central |
| 2026-08-12 | `a2133f1` | Remove `config.py`; credenciais viram parâmetros; remove os fluxos `logon_and_go_to_SCOM_A/SCPE_*`; adiciona `open_cics/open_rhelp`; **comenta a checagem de erro do `WD_SendKey`** e remove a do `WD_Wait` | Biblioteca genérica; erros "falsos" de rc atrapalhavam |
| 2026-08-17 | `c57df6d` | `find_python_32bit`, **auto-update**, `uuid4` no session_id, retry de socket p/ `WinError 10048/10054` | Facilidade de instalação e "sempre última versão" |
| 2026-08-18 | `2410936` | Auto-update baseado em commit (`git ls-remote` + `direct_url.json`), remove o retry de socket | Evitar reinstalar sempre |
| 2026-08-18 | `70f4fb3` | `textwrap.dedent` no script de checagem | Bug de indentação no `-c` |
| 2026-08-18 | `5c03d47` | `ping` antes de criar sessão; `sendall`; `wd_wait` sem `@timeout`; `assert` → `raise` | Robustez |

Fluxos removidos em `a2133f1` que ainda servem de exemplo de uso (CICS):
`SCOM` + função `A` em (20,11); `SCPE` + `ALT`/`INC`/`EXC` em (10,11), aguardando `INFORME` (23,7) e `VOLVO` (2,2).
O `run.py` original mostra a transação `SPRE` (custo padrão) — ver `git show 691c8c2:run.py`.

---

## 9. Limitações estruturais (resumo — detalhes em `docs/KNOWN_ISSUES.md`)

* O servidor 32-bit nunca é reiniciado após um auto-update → **pode rodar código antigo** (fura o requisito de "última versão").
* `.decode("utf-8")` / `.encode("ascii")` → telas com acentos quebram leitura/escrita.
* `buffer.value` trunca no primeiro `NUL` da tela.
* Códigos de retorno da DLL ignorados (`SendKey`, `Wait`, `ConnectPS` no RHELP).
* Chamadas podem travar para sempre dentro da DLL; o `@timeout` só abandona a thread.
* Sem logs (stdout/stderr do servidor vão para `DEVNULL` e não há handler configurado).
* Sem autenticação no socket; portas fixas compartilhadas entre usuários da mesma máquina.
* Dependência do Internet Explorer (COM) para abrir o terminal.

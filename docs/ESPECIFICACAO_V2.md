# RumbaPy v2 — Especificação final

> Status: **ESPECIFICAÇÃO FINAL PARA IMPLEMENTAÇÃO** (2026-10-03). Branch de trabalho: `dev` (nunca `master` sem pedido do dono).
> Base: proposta `docs/interno/design/proposta-experiencia/` + as quatro críticas (`critica-robustez.md` = **R#**,
> `critica-correcao.md` = **C#**, `critica-engenharia.md` = **E#**, `critica-implementabilidade.md` = **I#**).
> Precedência quando houver conflito: (1) pedido do dono; (2) fatos validados ao vivo (`docs/RUMBA_EHLLAPI.md` §11.1 e notas
> internas de validação); (3) este documento; (4) a proposta-base. A seção **M** registra a decisão sobre cada crítica.
> Documento **público**: não contém hostnames de mainframe nem dados internos. Onde for preciso citar a URL do portal,
> usa-se `<portal>`; o valor real continua sendo a constante já existente na v0.1.5.

Palavras normativas: **DEVE**, **NÃO DEVE**, **PODE**. Toda constante citada aqui é contrato: mudar exige editar este arquivo.

> **Ajustes de contrato já implementados** (P-0, 2026-10-03) estão consolidados em **A.8** e **prevalecem** sobre o texto
> das seções que eles citam. Implementadores das Ondas 1–3: leiam A.8 antes de programar contra B.5, B.7, C.4, F.1, F.3, F.4, J.2, J.3.

---

## A. Layout do pacote, responsabilidades e regras de camada

### A.1 Princípios

1. **Um só pacote, instalado só no Python 64-bit.** O worker 32-bit roda *a partir dos arquivos do pacote 64-bit*
   (`-I -S` + BOOT que **não** executa `rumbapy/__init__.py`, D.1). Só stdlib nos dois lados; sem pywin32/pywinauto.
2. **O worker é um proxy fino da DLL** (I22): só ele carrega a `ehlapi32.Dll`; cada op é curta (≤ 1 chamada de cada
   `WD_*`, salvo `snapshot`, `read_field` e `send`). **Janelas, launcher, fallback IE e `close_all` rodam no cliente
   64-bit** (ctypes `user32`/`kernel32`, que funcionam nas duas arquiteturas). Fechar terminais nunca depende de lease.
3. **Núcleo puro e testável**: codec, teclas, telas, esperas, classificação de janelas e decisão de update são Python puro.
4. **Fronteira única**: `Session` fala só com um `Backend` (B.3, C.5), que espelha 1:1 a tabela `OPS` (código em `_protocol.py`).
5. **Import ansioso e build_id ancorado** (E13, I13, R11): `import rumbapy` atualiza, importa **todos** os módulos do cliente
   e calcula `BUILD_ID` sobre os bytes em disco. Sem `__getattr__` preguiçoso.
6. **Update à prova de commit ruim** (E1): o caminho crítico do update mora em `_bootstrap.py` (congelado, só stdlib) e o
   `__init__` sabe se curar (`heal`, H.6).
7. **Testes nunca tocam produção** (E3): `RUMBAPY_SANDBOX=1` é respeitado pelo *código de produção* (DLL, launcher,
   `PostMessage`/`ShowWindow`). Ao vivo, só com `GuardedDll` (J.6).

### A.2 Árvore de arquivos (coluna "Pac." = pacote de implementação dono, seção L)

```
pyproject.toml                    P-F  name=rumbapy, version="2.0.0", requires-python=">=3.12", dependencies=[]
                                       build-system in-tree (H.7); SEM [project.optional-dependencies]
_build/rumbapy_build.py           P-F  backend PEP 517 só stdlib (wheel py3-none-any + sdist + editable)
.github/workflows/ci.yml          P-F  matriz + install-smoke (J.1)
README.md, CHANGELOG.md           P-G
src/rumbapy/
├─ __init__.py                    P-F  forma exata em A.6
├─ __main__.py                    P-F  CLI: python -m rumbapy {doctor | close <L> | version | update}
├─ _bootstrap.py                  P-F  CONGELADO (H.6): remote_master_sha, pip_install, verify_fresh, heal, say, log_line
├─ _role.py                       P-F  detect() -> "user" | "cli" | "legacy-shim"
├─ _version.py                    P-0  __version__="2.0.0"; PROTOCOL=2; REPO_OWNER, REPO_NAME, REPO_URL, MASTER_REF
├─ _update.py                     P-F  política de update (H), usa _bootstrap
├─ _doctor.py                     P-D  doctor()
├─ _log.py                        P-0  formatos, setup_client_logging(), notice(), SafeRotatingFileHandler
├─ _paths.py                      P-0  pastas, nomes de pipe/evento/locks, atomic_write(), read_json()
├─ _locks.py                      P-0  file_lock(name, timeout) com msvcrt.locking
├─ _buildid.py                    P-0  compute(pkgdir) -> str (puro)
├─ _protocol.py                   P-0  OPS (código), ADMIN_OPS, limites, validate_request(), encode()/decode()
├─ _types.py                      P-0  Profile, OpenInfo, WindowInfo, SessionEntry, SessionStatus, ConnectResult,
│                                      ReadResult, FieldRead, SendResult (+ to_wire/from_wire)
├─ _codec.py                      P-0  cp1252, NUL→espaço, NFC, conjunto seguro, pos<->(linha,col)
├─ _ipc.py                        P-B  pipe com DACL, autenticação HMAC com prazos, framing JSON (cliente e worker)
├─ _win32.py                      P-B  kernel32/advapi32/iphlpapi + 3 funções user32 do worker (D.9); assinaturas em P-0
├─ _user32.py                     P-C  Protocol User32 (congelado em P-0) + RealUser32 (ctypes, respeita sandbox)
├─ _instances.py                  P-C  puro: classify(), instâncias do perfil, diálogos, sanitize()
├─ _launcher.py                   P-C  open/attach/close orquestrados no cliente (E)
├─ _legacy.py                     P-C  neutralização de servidores 0.1.5 (E.8)
├─ _ie_open.js                    P-C  cópia do script validado ao vivo (E.7)
├─ _bridge.py                     P-D  WorkerHandle, get_worker(), spawn, handshake (lado cliente), reciclagem
├─ _backend.py                    P-0 (Protocol Backend) / P-D (WorkerBackend)
├─ _runtime32.py                  P-D  descoberta/validação do Python 32-bit (I)
├─ errors.py                      P-0  hierarquia completa (B.7) + to_wire/from_wire
├─ keys.py                        P-0 (Key, AIDS, tokenize_mnemonic) / P-A (tokenize_extra, split_at_aids, encode_body)
├─ screen.py                      P-0 (dataclasses, OIA.from_raw) / P-A (métodos de Screen)
├─ waits.py                       P-A  motor de espera puro (G)
├─ profiles.py                    P-0  PROFILES D/Z/A, resolve(), launcher_arg(), index_url()
├─ session.py                     P-E  Session, open(), attach(), close()
├─ recipes/__init__.py, cics.py, ims.py   P-E
├─ compat.py                      P-E  RumbaClient (API v0.1.5)
├─ client.py                      P-E  módulo legado (reexporta RumbaClient, Mnemonic, funções legadas)
├─ server.py                      P-E  stub para clientes 0.1.5 em memória (B.9.4) — sem DLL
├─ api.py                         P-E  stub: levanta ImportError explicativo
└─ _worker/
   ├─ __init__.py                 P-B  vazio
   ├─ main.py                     P-B  argv, ordem de boot, códigos de saída (D.2)
   ├─ server.py                   P-B  pipe, threads, lease, fila, eventos (D.3)
   ├─ dispatch.py                 P-B  handlers das ops, validação por _protocol.OPS
   ├─ engine.py                   P-B  estado da sessão HLLAPI (D.5)
   ├─ hllapi.py                   P-B  load_dll(), Hllapi (F.2)
   ├─ rc.py                       P-B  check(func, rc, ctx) -> None | exceção (F.7)
   ├─ structs.py                  P-B  parse QuerySessions/QuerySessionStatus (puro)
   ├─ mask.py                     P-B  máscara de campos ocultos e spans secretos (D.6, puro)
   ├─ watchdog.py                 P-B  T-watch (D.4)
   └─ guard.py                    P-0  `git mv tests/fakes/guard.py` (lógica inalterada)
tests/
├─ conftest.py                    P-0  sandbox (J.2)
├─ fakes/                         fake_ehllapi.py (existente + extensões aditivas de P-0, J.3), guard.py (reexporta
│                                  rumbapy._worker.guard), fake_user32.py (P-0), scenarios.py (P-0), fake_backend.py (P-B)
├─ unit/<pacote>/...              cada pacote
├─ e2e/...                        P-G (worker real em subprocess com --fake-dll)
└─ live/...                       P-G (só manual, RUMBAPY_LIVE=1 + guard)
```
Removidos: lógica de `api.py` (pywin32/pywinauto/IE COM), `src/rumbapy.egg-info/` (`git rm -r --cached`, E11), extra `server32`.

### A.3 Responsabilidades e proibições

| Módulo | Faz | NÃO DEVE |
|---|---|---|
| `_bootstrap` | rede/pip/verificação/heal mínimos | importar qualquer `rumbapy.*` |
| `_update` | política de update (H) | reexecutar o script (cortado, H.5) |
| `_bridge` | ciclo de vida de workers e IPC do cliente | conhecer telas, janelas ou receitas |
| `_launcher` | abrir/fechar/anexar (E) | chamar a DLL (só via `Backend`) |
| `session` | API pública, estado `ok/uncertain`, prazos | ctypes; acessar `_bridge` direto (só via fábrica de Backend) |
| `waits` | condições e polling | I/O |
| `_worker/*` | tudo que toca a DLL | importar `session`, `_bridge`, `_update`, `_launcher`, `recipes`, `profiles` |
| `recipes/*` | fluxos de logon e detectores de erro | acessar `Backend` sem `Session` |
| `compat` | semântica v0.1.5 sobre `Session` | lógica nova |

### A.4 Regras de camada (`tests/unit/test_layering.py`, via `ast`)

1. `_worker/*` importa só: stdlib, `rumbapy._worker.*`, `rumbapy._protocol`, `rumbapy._ipc`, `rumbapy._codec`,
   `rumbapy._buildid`, `rumbapy._win32`, `rumbapy._paths`, `rumbapy._log`, `rumbapy._types`, `rumbapy.errors`, `rumbapy.keys`.
2. `_bootstrap` importa só stdlib. `_update` importa só stdlib, `_bootstrap`, `_version`, `_paths`, `_log`, `_locks`, `errors`.
3. `errors`, `keys`, `screen`, `waits`, `_codec`, `_types`, `_protocol`, `_buildid`, `_instances`, `_worker/structs`,
   `_worker/mask` não importam `ctypes`. `errors` só importa `screen` sob `if TYPE_CHECKING:`.
4. Ninguém usa `ctypes.windll`/`ctypes.cdll` (só `ctypes.WinDLL(nome, use_last_error=True)` privado, E22).
5. Nenhum `print` fora de `__main__`, `_log.notice`, `_bootstrap.say` e `_doctor.doctor` (A.12 i). Nenhum `pickle`/`.send(`/`.recv(` de `Connection`.
6. Nenhum módulo importa `pandas`. Compatibilidade de API com 3.12 é garantida por `pyright` (`pythonVersion="3.12"`, E15).
7. `keys.AIDS` == conjunto de AIDs de `tests/fakes/fake_ehllapi.py` == de `rumbapy._worker.guard` (C10, teste de contrato).

### A.5 Nomes e caminhos (fonte única: `_paths.py`)

| Item | Valor |
|---|---|
| Raiz | `HOME = %LOCALAPPDATA%\rumbapy\` (`RUMBAPY_HOME` sobrescreve; obrigatório nos testes) |
| `user_tag()` | `sha256(f"{SID}|{ProcessIdToSessionId(GetCurrentProcessId())}".encode()).hexdigest()[:12]` |
| Registro do worker | `run\worker-<tag>-<L>.json` (D.7) |
| Instâncias lançadas | `run\launched-<tag>-<L>.json` = `{"pids": [{"pid", "created", "at"}]}` (últimas 5) |
| Pipe | `\\.\pipe\rumbapy2-<tag>-<L>-<rand>` (`rand = secrets.token_hex(8)`, gerado pelo cliente no spawn, R3) |
| Evento de parada | `Local\rumbapy2-<tag>-<L>-stop` (manual-reset) |
| Locks (arquivo) | `locks\spawn-<tag>-<L>.lock`, `locks\update.lock`, `locks\key.lock`, `locks\py32.lock` (I43, R24) |
| Chave | `authkey.bin` (32 bytes) |
| Logs | `logs\client-AAAAMMDD.log`, `logs\worker-<tag>-<L>.log` (+`.1`..`.5`), `logs\update.log`, `logs\pip-<AAAAMMDD-HHMMSS>.log`, `logs\boot\worker-<tag>-<L>-<AAAAMMDD-HHMMSS-fff>.txt` (manter 5 por letra) |
| Estado do update | `update-state.json`, `heal-state.json` |
| Cache do runtime | `runtime.json` |
| Letra reservada | `"_"` (worker temporário do `doctor`, nunca conecta; **não usada na v2.0**, A.12 h) |

`atomic_write(path, data: bytes)`: grava `path.tmp-<rand>`, `os.replace` com até 20 tentativas de 50 ms em
`PermissionError` (R23). `read_json(path) -> dict | None`: ausente/JSON inválido → `None` + WARNING. Pastas criadas sob demanda.

### A.6 `__init__.py` (forma exata)

```python
"""RumbaPy — automação de terminais 3270 do RUMBA. Leia: help(rumbapy.open)."""
import sys as _sys
try:
    from . import _role
    _ROLE = _role.detect()
    if _ROLE in ("user", "cli"):
        from . import _update
        _update.ensure_latest(context="import")
    from ._version import __version__
    if _ROLE in ("user", "cli"):
        from . import _buildid
        BUILD_ID = _buildid.compute(__path__[0])
        from . import errors, keys, screen, waits, profiles, session, compat, client, recipes  # ansioso (E13)
        from . import _bridge, _backend, _launcher, _runtime32, _doctor, _legacy               # noqa: F401
        if _buildid.compute(__path__[0]) != BUILD_ID:
            raise errors.UpdateRequired("O RumbaPy foi atualizado durante a importação. Rode de novo.")
except BaseException as _e:
    if getattr(type(_e), "_rumbapy_error", False) or isinstance(_e, (SystemExit, KeyboardInterrupt)):
        raise
    _r = globals().get("_ROLE") or ("legacy-shim" if _sys.orig_argv[1:3] == ["-m", "rumbapy.server"] else "user")
    if _r not in ("user", "cli"):           # _role pode ser o módulo quebrado: papel calculado inline
        raise
    from . import _bootstrap
    _bootstrap.heal(_e)                      # sempre levanta (H.6)

if _ROLE in ("user", "cli"):
    from .session import open, attach, close
    from ._doctor import doctor
    from .compat import RumbaClient
    from .keys import Key
    from .screen import Screen, Field, Cursor, OIA
    from .errors import *                    # noqa: F403  (errors.__all__)
    __all__ = ["RumbaClient", "Key", "Screen", "Field", "Cursor", "OIA", "doctor", *errors.__all__]
    # open/attach/close ficam FORA de __all__ (I34): use rumbapy.open(...)
```
`rumbapy.client`, `rumbapy.errors`, `rumbapy.profiles` são atributos (submódulos já importados), como na v0.1.5.

### A.7 Papéis (`_role.detect()`, só stdlib, sem efeitos)

| Papel | Condição | Update | Pode operar terminal |
|---|---|---|---|
| `legacy-shim` | `sys.orig_argv[1:3] == ["-m", "rumbapy.server"]` | não | não (o stub não carrega DLL) |
| `cli` | `sys.orig_argv[1:3] == ["-m", "rumbapy"]` | sim (E16) | sim |
| `user` | todo o resto (inclusive Python 32-bit rodando script direto) | sim | sim |

Não existe papel `worker` nem `RUMBAPY_ROLE` (C6): o worker nunca executa `__init__`. `session.open/attach/close` e
`RumbaClient()` levantam `RumbaError("uso interno: papel legacy-shim")` se `_ROLE` não for `user`/`cli`.

### A.8 Ajustes de contrato implementados no P-0 (prevalecem)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | A.4.1 | `_worker/*` também PODE importar `rumbapy._locks` (idle do watchdog, D.4e). Versão/protocolo no worker vêm de `_protocol.VERSION`/`_protocol.PROTOCOL` (não importar `_version`). |
| b | A.5 | `_paths.home()` é sempre absoluto (`RUMBAPY_HOME` relativo é resolvido contra o cwd); com `RUMBAPY_SANDBOX=1` e sem `RUMBAPY_HOME` → `SandboxViolation`. `read_json`: arquivo **ausente** → `None` com log DEBUG; ilegível/JSON inválido/não-objeto → `None` + WARNING; `PermissionError` → até 20 tentativas de 50 ms. `user_tag()` com sandbox inclui `HOME` no hash (nomes globais dos testes nunca colidem com produção). |
| c | B.5 | `Field` ganha `rows: int \| None = None` e `cols: int = 80` (só por nome; `repr=False, compare=False`), preenchidos por `Screen.from_wire`. `Field.end` = última posição de dados **inclusive**, dá a volta no fim da tela; `length == 0` → `start` (implementado no P-0). `Field.text`, `ReadResult.text`, `FieldRead.text` com `repr=False`. `Screen.__repr__` = resumo **sem conteúdo** (`<Screen D 24x80 · cursor (22,56) · teclado livre>`). `OIA.from_raw` levanta `TypeError` para não-bytes. `Screen.from_wire(d, terminal, *, rows=None, cols=None)` **exige** as chaves `busy` e `hidden_unknown` (ausentes → `ProtocolError`); posições aceitas como `[row, col]` ou linear. |
| d | B.7 | Cada classe tem `templates`/`hints` com os textos exatos e `Classe.make(variante="default", **campos)`. `TerminalInUse` variantes `lease`, `rc` (`{extra}` já formatado) e `rc_stuck` (texto da C.8). `UpdateFailed` variantes `default` (texto da B.7 + " Para reinstalar à mão, rode: <comando>" **na mensagem**) e `checkout` (texto literal da H.2, também para editable). |
| e | C.4 | `_protocol.validate_request(req, *, rows=None, cols=None)` / `validate_args(...)`: **com** a geometria, posição fora da tela (inclusive 0/negativos) e span de `read` → `InvalidPosition`; **sem** a geometria a posição não é conferida (nunca `ParameterError` por posição). O worker DEVE passar `rows/cols` da sessão conectada. Com `secret=True` as mensagens não citam bytes nem total de chunks. |
| f | E.5 | `OpenInfo.via ∈ {"launcher", "ie", "reuse", "attach"}`. |
| g | F.1 | `hllapi.load_dll(path, *, fake=None, guard="none")` checa o sandbox antes de tudo e usa `hllapi.new_policy(guard)` (cópia nova de `POLICIES[guard]`; nunca a instância compartilhada). |
| h | F.3 | `SessionStatus.eab` e `ConnectResult.eab` são **bool** (`bool(b[13] & 0x80)`). |
| i | F.4 | `check_span(pos, length, ...)` com `length < 1` → `ParameterError`; fora da tela → `InvalidPosition` citando a posição que ficou de fora; argumentos não inteiros (inclusive `bool`) → `ParameterError`. |
| j | J.2 | O `conftest.py` raiz tem sentinelas (`SENTINEL.is_guarded/function_check`, limpeza via `atexit`) contra: carga das DLLs HLLAPI, mensagens/ativação/entrada a janelas reais (user32), criação de processos do RUMBA/launcher/IE/cscript/shell/taskkill, worker com DLL real sem `--fake-dll`, `os.kill` fora dos filhos e `CTRL_C_EVENT`. |
| k | J.3 | O `FakeEhllapi` aceita `@H` (HELP) como tecla local e aceita `@R` (RESET) com teclado inibido mesmo sem `AUTORESET` (como o RESET real). O *golden master* do fake fica em `tests/unit/contracts/golden/` (teste garante que não é ignorado pelo Git). |
| l | — | Arquivos novos usam `# ruff: noqa: I001` (a convenção de imports por comprimento decrescente não é expressável no isort do ruff); E/F/W continuam checados. |

### A.9 Ajustes de contrato implementados na Onda 1 (P-A, P-B, P-F; prevalecem)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | B.6 | Nomes de tecla não diferenciam maiúsculas; **mnemônicos diferenciam** (`"@a"` = PF10, `"@A@F"` = ERASE_INPUT). A forma compacta (`PAGEDOWN`, `ERASEEOF`, `FIELDEXIT`) só vale escrita **sem** separadores: `"<P F 1>"`, `"<E-N-T-E-R>"`, `"<pa 1>"` e `"<PF_3>"` são `UndefinedKey` (texto incomum entre `<>` nunca vira AID). `encode_body` aceita uma `str` sozinha como corpo de um token. |
| b | B.6, B.7 | `keys._tokenize_extra(s, *, secret)` (privado; `tokenize_extra(s)` = `secret=False`): com `secret=True` a `UndefinedKey` nunca cita o trecho. Sem `secret`, o trecho `<...>` só é citado se houver um nome de tecla parecido (`<Entr>` → "Você quis dizer <ENTER>?"); senão a mensagem diz `'<...>'`. `tokenize_mnemonic` cita no máximo o código (`'@A'`, ou `'@A@Z'` se o 3º caractere for `@`). |
| c | G.2, G.4 | `wait_until(..., exc=WaitTimeout, hint=None)`: a mensagem do timeout é **exatamente** `describe(screen)` (o motor não acrescenta o OIA: quem descreve termina com `oia.describe()` onde a G.4 pede); `hint` vai para `exc.hint`. `HostConnectionLost` do motor tem `delivery="unknown"` quando `exc` é `NoHostResponse` (ou subclasse: a AID já saiu) e `None` nas demais esperas. `find(case=False)` usa `re.IGNORECASE` (não `casefold`) para manter a posição do *match*. |
| d | B.5, G.6 | O rodapé do `to_text` ganha "host ocupado" e "máscara desconhecida". `waits.py` exporta também `SCREEN_LOAD_TIMEOUT`, `HOST_LOST_AFTER`, `CICS_ERROR_TEXT/AT` e `cics_error_check` (detector da G.6). |
| e | C.1 | O pipe é criado com `PIPE_REJECT_REMOTE_CLIENTS`. `PipeNotFound`/`PipeTimeout`/`AuthRejected` são internas do transporte (`_ipc`), nunca atravessam o fio nem estão em `errors.__all__`: o `_bridge` as traduz (C.8). |
| f | C.4, D.5, D.7 | `nul_at`/`nul_tail` e o `sha` do DEBUG são calculados sobre os bytes **já mascarados**. `Engine.sessions()` devolve `{"sessions": [...]}` (a forma do resultado da C.4). WARNING para rc tolerado só em connect/disconnect/NORESET; rc 4/5 de leitura aparece só como `rc=N` na linha INFO. |
| g | D.7 | Log do `send` sem `secret`: `text_units=omitido` se houver texto depois de uma tecla (ex.: `<Tab>`) **ou** se a tela pode ter campo oculto (máscara desconhecida ou com spans ocultos, `Engine.screen_may_hide`); `text_units=0` continua visível. O `write` num campo oculto é secreto a partir da leitura do atributo (o tamanho nunca vai ao log, nem nos caminhos de erro). |
| h | D.6 | Limites assumidos: a conferência por hash de escrita espontânea do host só existe no `snapshot` (`read`/`read_field` não reenumeram por escrita sem AID); um span `secret` num campo **visível** cai quando o host responde à AID seguinte. Ou seja, `secret=True` protege a leitura só até a resposta da próxima AID: senha vai em campo oculto (README, P-G). Sem como conferir a máscara, a leitura é recusada com `HllapiError` "...a leitura foi recusada para não expor conteúdo oculto." (`rc`/`function` preservados); só rc 1/9/12/36 viram a exceção usual deles. |
| i | C.6.1, C.6.3, C.6.5, D.3 | O PID do pipe tem de bater com o `client_pid` do `hello_ack` (senão fecha). Com o PID do pipe **ilegível**, a conexão nunca toma um lease existente (`busy`); sem dono, entra. `shutdown` é permitido ao próprio dono (só um dono **diferente** do solicitante dá `TerminalInUse`). Op que exige `hwnd` sem `bind_instance` com o worker conectado e com hwnd conhecido → `ParameterError`; se o próprio worker não tem hwnd (`GetSessionHWND` = 0), `None` == `None` é a mesma instância. Só a T-conn fecha o próprio canal. |
| j | D.2, D.4 | `--fake-dll` sem `RUMBAPY_SANDBOX=1` → saída 1 (não 17). No disparo, o watchdog **cancela o job em curso** (o `send` para antes do próximo chunk: nenhuma AID depois de o cliente receber `WorkerHung`) e recusa qualquer chamada nova à DLL (`JobCancelled`) até o `os._exit`. `Engine.disconnect` deixa o estado desconectado mesmo se `DisconnectPS` falhar com rc genérico. `UndefinedKey` de `SendKey` rc 20 cita os **nomes** das teclas do chunk (nunca texto; `'?'` com `secret`). |
| k | A.2, H.4 | Marcador `update-in-progress.json` = `{pid, created, remote, at}` + `pip_pid`/`pip_created` assim que o pip nasce (`pip_install(sha, on_spawn=...)`); API extra do bootstrap: `marker_record`, `pip_alive`, `update_busy`. "Interrompida" = qualquer marcador achado **sob a trava** (depois de esperar um `pip_pid` vivo até `LOCK_TIMEOUT`; estourou → `UpdateFailed` "outra atualização não terminou (pip PID n ainda rodando)" e o marcador do outro **fica**). 1º Ctrl+C durante o pip espera o fim dele; o 2º sai sem esperar (o pip nunca é morto, E6). A sonda devolve `file/url/commit/version` (+ `error`, citado no motivo do `UpdateFailed`). |
| l | H.1, H.2 | TTL do `open`: REPL/Jupyter/IDLE/`python -i` = 1 h (mesmo critério do `{acao}`). `install_mode`: a distribuição casada vem **antes** do `.git` (venv dentro de um repositório é instalação normal); `.git` só conta sem distribuição ou com editable; editable pelo `.pth` (`build_editable`) → variante `checkout` com `mode=editable`. No papel `cli`, o import não checa: o `__main__` chama `ensure_latest("cli")` antes de `doctor`/`close`, `update` força, `version` só lê o estado. |
| m | H.6 | O heal só conta reinstalação com rc 0 na janela de 1 h; grava o marcador como o `_update`; reconfere o `pip_alive` **depois** de pegar a trava (um pip órfão pode ter nascido na espera); mensagem final pelo contexto (Jupyter/REPL/script, mesmos textos do `UpdateRequired`). |
| n | H.7 | O wheel leva só `.py`/`.js`/`.json`/`py.typed` de `src/rumbapy/` (outros tipos ficam de fora com aviso), os mesmos tipos do `build_id` (H.10). |

**Pendências da Onda 1 que a Onda 2/3 DEVE cumprir:**

* **P-D (`_bridge`)**: `hello_ack` com o `os.getpid()` real; `bind_instance(result.hwnd)` logo depois do `connect` (`None` é aceito quando o worker não tem hwnd); tratar leitura/snapshot recusados (`HllapiError` da A.9 h) sem repetir em laço; traduzir as exceções internas do `_ipc` (A.9 e); `load_or_create_key` já usa `locks\key.lock` (só passe `lock=` para substituí-la). Trocar a cópia do BOOT em `tests/unit/worker/helpers.py` por `rumbapy._bridge.BOOT` + teste de contrato de que é o texto da D.1.
* **P-E (`session`, `recipes`, `compat`)**: `send_keys(..., secret=True)` usa `keys._tokenize_extra(s, secret=True)`; corpos com mais de `MAX_CHUNKS` (64) chunks viram vários `send`; o `describe` passado a `wait_until` termina com `oia.describe()` onde a G.4 pede, usa os textos exatos da B.7/G.3 para `NoHostResponse` e o `keyboard` do `wait_ready`, omite o conteúdo da tela com `hidden_unknown`, e passa `hint=` (dica da G.4); espera depois de AID usa `exc=NoHostResponse`. `compat.RumbaClient.update_python_64bit` chama `_update.ensure_latest("legacy", force=True)` e nunca reexecuta. Senha sempre com `secret=True` (receitas).
* **P-G**: forma literal da A.6 no `__init__` (sem o laço `TODO(P-G)`), `WAVE2` → `REQUIRED` em `test_packaging.py`, pyright bloqueante no CI, job x86 rodando `tests/unit/worker/test_main_e2e.py`, README com o limite da A.9 h; antes do merge no `master`: teste manual do heal (H.6) e job `install-smoke` verde no GitHub Actions.

### A.10 Ajustes de contrato do P-C (Onda 2: janelas, launcher e legado; prevalecem)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | E.5, E.6 | `Launcher.open/attach` devolvem `Opened(backend, info, profile, screen)` (dono já com `bind_instance` + a 1ª tela estável); `open_terminal/attach_terminal` (métodos e funções de módulo) devolvem `(Backend, OpenInfo)`. |
| b | D.8, J.3 | `Launcher(user32, get_backend, spawn=None, *, clock, sleep, find_worker, find_launcher, run_script, neutralize, legacy_busy_extra, process_alive, process_created, hllapi_client_pids)`. `get_backend` aceita `(letra, modo)` ou `(modo)` já ligado à letra (decidido por `inspect.signature`). |
| c | E.5 | Passos 7 e 8 trocados: `bind_instance(hwnd)` logo depois do `connect`, antes do 1º `snapshot` (A.9 i). |
| d | E.5 (2, 6), E.6 | `SessionReplaced` no `connect` do dono (o handle em cache é o de uma `Session` anterior do mesmo processo, já despejada pelo `evict` do passo 3, ex.: célula `open('D')` rodada de novo): `release()` dele, novo `get_backend(L, "owner")` e outra tentativa, **uma vez**; um 2º `SessionReplaced` propaga (com a limpeza do passo 10). |
| e | E.5 (2) | `reuse`: o fallback (notice, fechar e abrir) só vale para falha de **conexão** (`get_backend`/`connect` com `TerminalError`/`HllapiError`, rc 1 por 10 s, ou HLLAPI em outra janela). Conectado, a 1ª tela tem o prazo `D` e o `LaunchTimeout` dela propaga **sem fechar** a janela reaproveitada. |
| f | E.5 (4, 10), E.7 | Falha do fallback IE no passo 4 também roda a limpeza do passo 10: `cscript` sem terminar → `LaunchTimeout` (fase "a letra não foi registrada", `details["via"] == "ie"`); `ok:false` → `LaunchFailed` com a mensagem `"O Internet Explorer não abriu o {perfil} ({step}: {error})."` (sanitizada; ainda fora dos templates de `errors.LaunchFailed` — pendência do dono do `errors.py`: variante `ie`). |
| g | E.5, C.6 | Toda falha de `open`/`attach` devolve o lease de dono (`release`). `open`/`attach`, e `close_all` quando ele mesmo abriu a conexão admin, dão `release()` nela ao terminar (o worker aceita no máximo `MAX_CONNECTIONS`); a admin passada pelo chamador, ou o mesmo objeto do dono, não é devolvida. |
| h | E.3 | Regra `launched`: só janelas **visíveis**, de imagem `launcher`/`iexplore`; entrada de `launched-*.json` sem `created` inteiro é ignorada, e `record_launch` não grava se `process_created` falhar. "Instância de outro perfil" (R6b) = qualquer título `<NOME> - Pro Client[ ...]` do launcher ou o padrão `ie` com outro nome (perfis do portal fora de `PROFILES` também contam). |
| i | E.3 | `hllapi`: `iexplore` filho de `msedge` continua de fora (I18). `Instance.title` e `Dialog.title/texts` têm `repr=False` (use `safe_title`/`sanitize`). |
| j | E.4 (6, 7), E.5 (5) | `InstanceNotClosable` sem `hwnd`/`hwnd_pid` no registro do worker: o PID sai de `_win32.hllapi_client_pids()` (só leitura), preferindo Edge > IE > launcher (nunca o worker nem o próprio processo). `ClientTableFull` também quando um `HLLAPI ERROR` aparece durante o registro (passo 5) ou num sobrevivente do `close`. |
| k | E.8 | Com `RUMBAPY_SANDBOX=1` e as portas padrão, a neutralização é pulada (WARNING). Repetição do passo 4: lê **todos** os PIDs ouvintes da porta (`GetExtendedTcpTable`); se algum for de outra sessão do Windows, nada é tocado; com 1 ouvinte, uma rodada; com 2+, até 3 rodadas de `ping`/`disconnect` (idempotente) enquanto um não-shim responder. |
| l | E.2 | `RealUser32` lê textos com `InternalGetWindowText` (não `GetWindowTextW`, que manda `WM_GETTEXT` e trava em janela pendurada). |

### A.11 Ajustes de contrato do P-E (Onda 2: API, receitas e compatibilidade; prevalecem)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | B.3, J.3 | Injeção: `session._set_factories(backend_factory=..., user32_factory=..., launcher_factory=...)` (`None` volta ao padrão). `backend_factory(profile, python32)` devolve `get_backend(letra, modo) -> Backend` (`modo` = `"owner"`/`"admin"`; padrão `_launcher._default_get_backend`); `user32_factory()` devolve o `User32`; `launcher_factory(user32, get_backend)` devolve o orquestrador (padrão `_launcher.Launcher`, A.10 b) com `open/attach` (→ `Opened`), `close_all` (→ nº fechadas) e `window_info`. |
| b | B.3 | `reconnect()`: com hwnd conhecido e `WD_GetSessionHWND = 0` na reconexão levanta `NotConnected` (`function="WD_GetSessionHWND"`, `details["motivo"]`), sem religar ao hwnd antigo; hwnd diferente → `replaced` + `SessionReplaced`. |
| c | B.3 | Em `replaced`, `close()` também levanta `SessionReplaced` (não fecha a instância NOVA, que pode ser de outra `Session` ou de outro script); `__exit__` com `close_on_exit` pula o `close()` (log INFO). Para fechar de propósito: `rumbapy.close(L)`. |
| d | G.5 | `wait_any`: valor `Callable[[Screen], Any]` satisfaz pela **verdade** do resultado (`""`, `0`, `[]`, `None` não satisfazem). Mensagem do timeout: `Esperei {s} s por uma destas opções: {opção}; {opção}. Linha 1 atual: '{linha1}'.` + OIA, com cada opção em `'{chave}' = '{texto}' em qualquer lugar (não encontrado)`, `'{chave}' = '{texto}' em ({r},{c}), ali está '{atual}'` ou `'{chave}' = condição própria (não satisfeita)`. |
| e | B.9.1 | `connected` é property: `True` só depois de `logon_*`/`connect` (ou anexo automático) **bem-sucedido** e enquanto a `Session` estiver conectada (`ok`/`uncertain`); falha de logon, `disconnect`, `close_terminal`, `replaced`/`closed` → `False`. |
| f | B.9.1 | `disconnect()` sem sessão não faz nada (sem anexo automático). `connect()` com a sessão `closed`/`replaced` faz novo `attach(L, timeout=60)`. `close_terminal()` com a sessão `replaced`/`closed` (ou sem sessão) chama `rumbapy.close(L)` (semântica da v0.1.5) e, se `replaced`, descarta a sessão (a próxima op anexa de novo). |
| g | B.9.1 | `python32bit_path` falso (`""`, `None`) = descoberta automática (como a v0.1.5). `copy_string_to_ps(y, x, "")` não faz nada e devolve `True` (v0.1.5 ignorava o rc; célula vazia não interrompe o lote). |
| h | B.7 | Pendência do dono do `errors.py`: `read` que termina com o host ocupado levanta `HostBusy` com o único texto de B.7 ("parte das teclas pode ter sido digitada"), enganoso numa leitura; falta uma variante `read` (ex.: "O host continua ocupado (X SYSTEM); a leitura não foi possível em {s} s."). |

### A.12 Ajustes de contrato do P-D (Onda 2: ponte, runtime 32-bit e doctor; prevalecem)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | D.1 | `--dll` só vai na linha de comando **sem** `--fake-dll` (no modo dublê a DLL real nem é citada; o worker usa `DLL_PATH` como padrão). `RUMBAPY_LOG_LEVEL` (`DEBUG`/`INFO`/`WARNING`/`ERROR`; outro valor → WARNING e `INFO`) define o `--log-level` do worker (diagnóstico). |
| b | C.5, C.8 | Falhas de disco do cliente nunca são "transporte": `OSError` ao ler/criar `authkey.bin` → `WorkerError` ("Não consegui ler/criar authkey.bin (…)"); `OSError` de `_buildid.compute` (handshake passo 4, R11b, `BUILD_ID`) → `WorkerError` ("Não consegui ler os arquivos do pacote rumbapy para conferir a versão (…)"). Só `send/recv` do canal e o `connect` do pipe contam como transporte ("gone"). |
| c | D.1, D.8 | `spawn`: `OSError` do `Popen` → `WorkerStartFailed`; `OSError` ao criar as pastas/arquivo de boot → `WorkerError` ("pasta de dados"); falha de `prune_boot_logs` só gera WARNING. |
| d | D.8 | A trava `spawn-<tag>-<L>` **não** é solta nas esperas (soltá-la no boot deixaria outro cliente subir um 2º worker da letra). Quem espera a trava espera em rodadas de 30 s até `SPAWN_LOCK_MAX_S` = 20 s × (2 + 3) + 10 s × (3 + 1) + 10 s = 150 s (o pior caso de quem segura); estourou → `WorkerError` "Outro processo está iniciando … há mais de 150 s". |
| e | D.2, D.4, B.7 | Saída 3 no boot usa o `exit_reason` do registro (mesmo `pipe`) como a tabela da D.2; sem motivo → `WorkerStartFailed`. `WorkerHung` montado pelo cliente a partir do `exit_reason`: `hung_function`/`hung_limit` do registro, se houver; senão `stop_while_hung` = "o encerramento (WD_DisconnectPS/WD_ResetSystem)" em 5 s, op `connect` = `WD_ConnectPS` em 15 s, demais = "uma chamada da DLL" em 20 s. |
| f | B.7, D.8 | `WorkerStartFailed` que não vem de código de saída (desistência, `Popen` falhou, não ficou pronto em 20 s) usa "O processo auxiliar 32-bit do terminal {L} não iniciou: {motivo}" (sem "código {n}"). Pendência do dono do `errors.py`: variantes `too_many`, `popen_failed`, `not_ready`. |
| g | D.8, C.7 | Pipe mudo com o lease do **próprio** processo no registro: não sinaliza a parada (o worker a adiaria); espera 1 s e tenta 1×; depois `WorkerError` "… o lease é deste mesmo processo …; não vou pará-lo." (dica: fechar as `Session`). `Bridge.find_handle` (E.4 passo 1) nunca sobe worker. |
| h | A.5, M.4 I11 | O `doctor` não usa worker temporário: lê os registros de D/Z/A e faz `ping` por conexão admin só nos vivos (`Bridge.probe`, sem spawn e sem criar a chave). A letra reservada `_` não é usada na v2.0. |
| i | A.4.5, B.2 | `doctor()` (função pública cujo contrato é imprimir, B.2) escreve em `sys.stdout` e está na lista de exceções do "nenhum print". |
| j | I.1 | Depois de estourar o prazo da sonda, a coleta da saída também tem prazo (1 s); nunca há espera sem prazo (neto de redirecionador de venv com o stdout herdado). |

### A.13 Fechamento da Onda 2 (orquestrador; prevalece)

| # | Seção | Contrato em vigor |
|---|---|---|
| a | A.6 | `__init__.py` na forma literal da A.6 (o laço transitório `TODO(P-G)` saiu): todo módulo do cliente é importado; arquivo ausente ou quebrado vai ao `heal`. |
| b | D.9, E.8 | `_win32.tcp_listener_pids(port) -> list[int]` (todos os ouvintes de `127.0.0.1`/`0.0.0.0`, sem repetição, só leitura); `tcp_listener_pid` = o 1º deles. O `_legacy` usa a função pública (o parser duplicado saiu). |
| c | J.1 | pyright (`basic`, 3.12, Windows) **bloqueante** no CI sobre `src` e `_build` (sem exclusões); avisos de `__all__` dinâmico tolerados. `python -m rumbapy doctor/close` chamam `rumbapy.doctor`/`rumbapy.close` direto. |

---

## B. API pública

### B.1 Convenções

* Coordenadas públicas `(row, col)` 1-based. Tempos em segundos (`float`). Métodos que mudam estado devolvem `self`.
* Toda falha levanta subclasse de `rumbapy.RumbaError` (que herda de `RuntimeError`).
* **Nada vai ao host sem pedido explícito.** Só `press/enter/clear/pf/pa/send_keys` com AID transmitem; `write/fill/type`
  alteram o PS local (o host recebe na próxima AID). `open/attach/close/release/disconnect/screen/read*/wait*` são locais.
* **Nunca há reenvio automático de entrada.** Só ops idempotentes de leitura são repetidas (1×) após reciclar o worker.

### B.2 Funções de módulo (`session.py`)

```python
WindowMode = Literal["normal", "minimized"]           # "hidden" cortado (I16)

def open(terminal: str = "D", *, reuse: bool = False, window: WindowMode = "normal",
         timeout: float | None = None, close_on_exit: bool = False,
         python32: str | os.PathLike | None = None) -> Session
def attach(terminal: str = "D", *, timeout: float = 10.0,
           python32: str | os.PathLike | None = None) -> Session
def close(terminal: str, *, timeout: float = 15.0) -> int
```
* `terminal`: letra (`"D"`, `"Z"`, `"A"`) ou nome do perfil (`"BRAZIL_D"`, `"IMS_A"`), sem diferenciar maiúsculas.
  Desconhecido → `UnknownTerminal`. `timeout=None` em `open` = `RUMBAPY_OPEN_TIMEOUT` ou 90 s.
* `open(reuse=False)` (padrão): **fecha TODAS as instâncias do perfil** (E.4) — inclusive as que outro script estiver
  usando: o lease dele é revogado (`evict`, C.6) e as `Session` dele passam a levantar `SessionReplaced` — e abre uma nova
  pelo Web Launcher (E.5). `reuse=True`: com exatamente 1 instância e conexão em ≤ 10 s, só conecta (`launched=False`);
  0 ou 2+ instâncias ou instância quebrada → mesmo caminho de `reuse=False`, com `notice` (C31). Não envia nada ao host.
* `attach`: **não destrutivo**. Letra não registrada e nenhuma instância → `TerminalNotOpen`; 2+ instâncias →
  `MultipleInstances` (dica: `rumbapy.open('D')`); outro processo com o lease → `TerminalInUse`.
* `close`: fecha todas as instâncias do perfil (E.4), revogando qualquer lease; não exige worker nem lease; devolve
  quantas janelas de instância fechou. Letra obrigatória. Usado também por `Session.close()` e `close_terminal()`.
* `doctor(*, verbose: bool = False) -> DoctorReport` (`_doctor.py`, F): imprime relatório em português.

Perfis fixos (`profiles.PROFILES`): `D → BRAZIL_D (cics)`, `Z → BRAZIL_Z (cics)`, `A → IMS_A (ims)`.
`register_profile` foi cortado (um perfil novo é uma linha em `profiles.py`).

### B.3 `Session`

```python
class Session:
    def __init__(self, backend: Backend, profile: Profile, info: OpenInfo, *,
                 timeout: float = 30.0, user32: User32 | None = None, close_on_exit: bool = False) -> None
    # injeção (I2; A.11 a): open/attach/close usam session._backend_factory(profile, python32) -> get_backend(L, modo),
    # session._user32_factory() e session._launcher_factory(user32, get_backend); testes trocam por
    # session._set_factories(backend_factory=..., user32_factory=..., launcher_factory=...).

    terminal: str; profile: Profile; info: OpenInfo; rows: int; cols: int
    timeout: float                                   # padrão das esperas (RUMBAPY_TIMEOUT ou 30 s), mutável
    state: Literal["ok", "uncertain", "replaced", "closed"]
    connected: bool                                  # property
    cics: Cics; ims: Ims                             # RecipeNotAvailable se o perfil não for do tipo
    error_checks: list[Callable[[Screen], None]]     # CICS: detector "E R R O   G R A V E" (G.6)

    # leitura (local; repete 1× após reciclagem)
    @property
    def screen(self) -> Screen                       # = snapshot()
    def snapshot(self, *, fields: bool = False) -> Screen
    def read(self, row: int, col: int, length: int) -> str          # exatamente `length` caracteres
    def read_field(self, row: int, col: int) -> str                 # campo que contém (row,col), tamanho real
    def find(self, text: str | re.Pattern, *, case: bool = True) -> Cursor | None   # = self.screen.find(...)

    # escrita local
    def write(self, row: int, col: int, text: str, *, secret: bool = False,
              errors: Literal["strict", "replace"] = "strict") -> Session
    def fill(self, row: int, col: int, text: str, *, align: Literal["left", "right"] = "left",
             secret: bool = False, errors: Literal["strict", "replace"] = "strict") -> Session
    def type(self, text: str, *, secret: bool = False) -> Session  # WD_SendKey sem AID, '@' escapado
    def move_to(self, row: int, col: int) -> Session

    # teclas
    def press(self, key: Key | str, *, timeout: float | None = None) -> Session
    def enter(self, **kw) -> Session; def clear(self, **kw) -> Session
    def pf(self, n: int, **kw) -> Session; def pa(self, n: int, **kw) -> Session
    def send_keys(self, keys: str, *, secret: bool = False, timeout: float | None = None) -> Session

    # esperas (G) — todas devolvem o Screen que satisfez a condição (Screen.match preenchido)
    def wait_ready(self, timeout: float | None = None) -> Screen
    def wait_text(self, text: str | re.Pattern, row: int | None = None, col: int | None = None, *,
                  timeout: float | None = None, gone: bool = False, case: bool = True) -> Screen
    def wait_any(self, options: Mapping[str, str | re.Pattern | tuple[str, int, int]], *,
                 timeout: float | None = None) -> Screen        # .match = chave
    def wait_change(self, *, since: Screen | None = None, timeout: float | None = None) -> Screen
    def wait_cursor(self, row: int, col: int, *, timeout: float | None = None) -> Screen

    # janela (cliente, user32; nunca ativa/rouba foco)
    def show(self) -> Session                       # SW_SHOWNOACTIVATE (4)
    def minimize(self) -> Session                   # SW_SHOWMINNOACTIVE (7)
    @property
    def window(self) -> WindowInfo

    # ciclo de vida
    def acknowledge(self) -> Session                # sai de "uncertain" (o script assume a tela atual)
    def release(self) -> None                       # devolve o lease; HLLAPI continua conectado no worker
    def disconnect(self) -> None                    # WD_DisconnectPS + release
    def reconnect(self, timeout: float = 10.0) -> Session
    def close(self) -> None                         # = rumbapy.close(letra); state="closed" (em replaced: SessionReplaced, A.11 c)
    def __enter__(self) -> Session
    def __exit__(self, *exc) -> None                # release(); se close_on_exit: close(). Nunca mascara exceção.
    def __repr__(self) -> str                       # <Session D BRAZIL_D 24x80 ok worker=1234> (sem conteúdo de tela)
```

Quem libera o quê (I8):

| Chamada | Lease (conexão do pipe) | HLLAPI no worker | Janelas |
|---|---|---|---|
| `release()` / `__exit__` / GC (`weakref.finalize`) / `atexit` | devolve se nenhuma outra `Session` conectada da letra no processo | mantém | mantém |
| `disconnect()` | devolve | `WD_DisconnectPS` | mantém |
| `close()` / `rumbapy.close()` | revoga (de qualquer processo) | desconecta (`evict`) | fecha todas do perfil |
| fim do processo | o SO fecha o pipe → worker libera | mantém (idle 30 min) | mantém |

Máquina de estados da `Session` (R17, C12, C16, I45):

| Estado | Entra quando | Efeito | Sai quando |
|---|---|---|---|
| `ok` | criada; espera de conteúdo bem-sucedida | normal | — |
| `uncertain` | exceção com `delivery in {"partial","unknown"}`; `NoHostResponse`; `HostConnectionLost`; `KeyboardInterrupt` durante `write/send/set_cursor` | `write/fill/type/move_to/press/send_keys` levantam `SessionUncertain` **sem tocar a DLL**; leituras e esperas funcionam | `wait_text/wait_any/wait_cursor/wait_change` bem-sucedido, ou `acknowledge()` |
| `replaced` | worker responde `SessionReplaced` (instância diferente, `evict`) ou outra `open()` do mesmo processo na letra | toda op levanta `SessionReplaced` | nunca (crie outra Session) |
| `closed` | `close()` | toda op levanta `TerminalNotOpen` | nunca |

### B.4 Regras que o usuário pode confiar (README)

1. Antes de toda tecla espera-se o teclado livre (G.3); antes de toda AID, **tela estável** (2 snapshots iguais e livres).
2. A AID vai **sozinha** no último `WD_SendKey`; a espera pós-AID compara com a tela **depois** da digitação (G.3).
3. Uma exceção em entrada diz `delivery`: `"none"` (nada chegou ao RUMBA), `"partial"` (parte das teclas/texto pode ter
   sido aceita), `"unknown"` (não sei). **Nunca repita uma AID dentro de `except`; reancore pela tela.**
4. Escrita que não cabe no campo é recusada **antes** de copiar (`Truncated`, `delivery="none"`) (C9).
5. Conteúdo de campos não exibidos (senha) **nunca** sai do worker: `Screen`, `read`, exceções, `_repr_html_` e logs
   veem espaços (D.6). Texto escrito/digitado nunca é registrado em log, em nível nenhum.
6. Leituras têm o tamanho pedido (NUL → espaço); acentos corretos (cp1252); NFC na escrita.
7. Ctrl+C interrompe esperas na hora; durante uma op de entrada, a sessão fica `uncertain`.
8. `open`/`close` fecham **todas** as instâncias do perfil, de qualquer script; `attach` nunca fecha nada.
9. Nenhuma ação da biblioteca põe o terminal em primeiro plano nem ativa a janela.

### B.5 `Screen`, `Field`, `Cursor`, `OIA` (`screen.py`, puro)

```python
class Cursor(NamedTuple):
    row: int; col: int
    def __str__(self) -> str                  # "(22,56)"

@dataclass(frozen=True, slots=True)
class Field:
    start: Cursor        # 1ª posição de dados (após o byte de atributo)
    length: int          # WD_FindFieldLength (0 se rc 28)
    attribute: int       # byte bruto 0xC0..0xFF
    text: str            # do snapshot; campo oculto => espaços
    row/col/end (properties); protected = attr & 0x20; numeric = attr & 0x10
    hidden = (attr >> 2) & 3 == 3; intensified = (attr >> 2) & 3 == 2; modified = attr & 0x01
```
Nota de fato (C29a): `0xC5` = desprotegido, intensidade `01` (normal **detectável por caneta**), MDT=1.

```python
@dataclass(frozen=True, slots=True)
class OIA:
    raw: bytes                    # 103 bytes de WD_CopyOIA
    @classmethod
    def from_raw(cls, raw: bytes) -> OIA          # ValueError se len != 103
    format: int                   # raw[0] (1 = 3270)
    group1: int                   # raw[81]
    inhibit: bytes                # raw[88:93] (G8, 5 bytes)
    online: bool                  # (group1 & 0x30) != 0  (SSCP-LU 0x20 | LU-LU 0x10; ao vivo 0x10) [a validar SSCP-LU]
    keyboard_locked: bool         # any(inhibit)  (validado: zerado com teclado livre)
    ready: bool                   # online and not keyboard_locked
    def describe(self) -> str     # "teclado livre" | "teclado bloqueado (G8=00 20 00 00 00)" | "sem conexão com o host"
```
A imagem `raw[1:81]` usa códigos internos: **nunca** decodificar como texto. Bits individuais de G8, G7 (insert) e G12
(erro de comunicação) ficam **fora** da v2.0 até validação (K.3).

```python
@dataclass(frozen=True, slots=True)
class Screen:
    terminal: str; rows: int; cols: int
    text: str                     # exatamente rows*cols caracteres, já mascarado pelo worker (D.6)
    cursor: Cursor
    oia: OIA                      # sempre presente (CopyOIA falhou => exceção)
    host_busy: bool               # CopyPS devolveu rc 4/5
    hidden_unknown: bool          # a máscara não pôde ser calculada (D.6): não registre/anexe esta tela
    fields: tuple[Field, ...] | None   # só com snapshot(fields=True)
    taken_at: float               # time.time()
    match: Cursor | str | None = None  # preenchido pelas esperas
    @classmethod
    def from_wire(cls, d: dict, terminal: str) -> Screen
    lines: list[str]  (property); def line(self, row) -> str; def get(self, row, col, length) -> str
    def find(self, text: str | re.Pattern, *, case: bool = True) -> Cursor | None    # busca linear em self.text
    def contains(self, text: str, row: int | None = None, col: int | None = None) -> bool
    def __contains__(self, text: str) -> bool
    def field_at(self, row, col) -> Field | None          # ParameterError se fields is None
    input_fields: list[Field]  (property)                 # desprotegidos, em ordem
    def table(self, first_row, last_row, columns: Mapping[str, tuple[int, int]], *,
              strip: bool = True, skip_blank: bool = True) -> list[dict[str, str]]   # (coluna, tamanho)
    def to_text(self, *, ruler: bool = True) -> str        # régua de colunas + nº de linha + rodapé
    def __str__(self) -> str                               # to_text()
    def _repr_html_(self) -> str                           # <pre> com html.escape e cursor destacado
```
Rodapé do `to_text`: `cursor (22,56) · teclado livre · 24x80 · D BRAZIL_D · 14:05:12`. Cortados (I24): `region`,
`find_all`, `diff`, `to_dataframe` (use `pd.DataFrame(tela.table(...))`).

### B.6 `Key` (`keys.py`)

```python
class Key(Enum):                       # sem mixin str (E24): Token = str | Key nunca é ambíguo
    ENTER="@E"; CLEAR="@C"; PA1="@x"; PA2="@y"; PA3="@z"; SYSREQ="@A@H"; ATTN="@A@Q"
    PF1="@1" … PF9="@9"; PF10="@a"; PF11="@b"; PF12="@c"; PF13="@d" … PF24="@o"
    PAGE_UP="@u"; PAGE_DOWN="@v"                       # AID (C10, I14): roll transmite ao host
    RESET="@R"; TAB="@T"; BACKTAB="@B"; HOME="@0"; END="@q"; NEWLINE="@N"
    UP="@U"; DOWN="@V"; LEFT="@L"; RIGHT="@Z"; BACKSPACE="@<"; DELETE="@D"; INSERT="@I"
    ERASE_EOF="@F"; ERASE_INPUT="@A@F"; FIELD_EXIT="@A@E"; CURSOR_SELECT="@A@J"
    DUP="@S@x"; FIELD_MARK="@S@y"; PRINT="@P"; HELP="@H"
    # aliases (mesmo valor): F1..F24 = PF1..PF24; PAGEUP=PAGE_UP; PAGEDOWN=PAGE_DOWN; ERASEEOF=ERASE_EOF
    @property
    def is_aid(self) -> bool                            # self in AIDS
    @classmethod
    def parse(cls, name: "Key | str") -> Key            # "ENTER","PF3","F3","PAGE_UP","PAGEUP","<Pf3>","@3"; sem caixa
    @classmethod
    def pf(cls, n: int) -> Key                          # 1..24, senão ParameterError

AIDS: frozenset[Key] = {ENTER, CLEAR, PF1..PF24, PA1..PA3, SYSREQ, ATTN, PAGE_UP, PAGE_DOWN}
Token = str | Key                                       # str = texto literal

@dataclass(frozen=True)
class Segment:
    body: tuple[Token, ...]                             # texto e teclas locais, sem AID
    aid: Key | None

def tokenize_extra(s: str) -> list[Token]       # "abc<Tab>1<Enter>"; "<<" = "<" literal; nome inválido -> UndefinedKey
def tokenize_mnemonic(s: str) -> list[Token]    # "SPRE@E"; "@@" = "@"; "@" final ou código inválido -> UndefinedKey
def split_at_aids(tokens: Sequence[Token]) -> list[Segment]
def encode_body(body: Sequence[Token], *, host: Literal["cics", "ims"],
                errors: Literal["strict", "replace"] = "strict", secret: bool = False) -> list[str]
    # unidades atômicas = 1 caractere (com "@" -> "@@") ou 1 mnemônico; chunks <= 128 bytes cp1252;
    # nunca parte uma unidade; devolve str = bytes.decode("cp1252") (formato de wire, C.3)
```
`Key.parse` com nome inválido → `UndefinedKey` com sugestão (`difflib.get_close_matches`, "Você quis dizer PF3?").
Regra de implementação: sempre testar `isinstance(t, Key)` **antes** de `isinstance(t, str)`.

### B.7 Exceções (`errors.py`)

```python
class RumbaError(RuntimeError):
    _rumbapy_error = True                     # usado pelo __init__ (A.6)
    code: ClassVar[str]                       # = nome da classe (nome de wire estável)
    def __init__(self, message: str, *, hint: str | None = None, rc: int | None = None,
                 function: str | None = None, terminal: str | None = None, screen: Screen | None = None,
                 delivery: Literal["none", "partial", "unknown"] | None = None, details: dict | None = None)
        # super().__init__(message) com UM argumento (E23); define __reduce__
    message, hint, rc, function, terminal, screen, delivery, details   # atributos
def to_wire(exc: RumbaError) -> dict   # {"type","message","hint","rc","function","delivery","details"}; nunca screen
def from_wire(d: dict, *, terminal: str | None = None) -> RumbaError   # tipo desconhecido -> RumbaError
```
`__str__` (formato fixo):
```
[D] O RUMBA recusou as teclas (teclado bloqueado); parte delas pode ter sido digitada.
  Como resolver: confira a tela (print(t.screen)) antes de repetir qualquer tecla; se houver 'X -f', use t.press(Key.RESET).
  (WD_SendKey rc=5; entrega: parcial)
```

| Classe | Base extra | rc | `delivery` | Mensagem (texto exato; `{}` = campo) |
|---|---|---|---|---|
| `SetupError` / `UnsupportedPlatform` | | | | "O RumbaPy só funciona no Windows." |
| `RumbaNotInstalled` | | | | "Não encontrei o RUMBA ({o_que}) em {caminho}." |
| `Python32NotFound` | `ValueError` | | | "Não encontrei um Python 32-bit >= 3.12 (o RUMBA só existe em 32-bit). Motivo: {motivo}." |
| `SandboxViolation` | | | | "Bloqueado pelo modo sandbox dos testes: {acao}." |
| `UpdateError` / `UpdateRequired` | | | | "O RumbaPy foi atualizado para {versao} ({sha7}). {acao}" (`acao` por contexto, H.5) |
| `UpdateCheckFailed` | | | | "Não consegui verificar a versão mais recente no GitHub ({motivo}). Conecte-se à rede/VPN e rode de novo. Para fechar terminais abertos agora, feche as janelas '<PERFIL> - Pro Client' pelo X." |
| `UpdateFailed` | | | | "A atualização do RumbaPy falhou ({motivo}). Detalhes em {log}." + comando de recuperação (H.4) |
| `WorkerError` / `WorkerStartFailed` | | | | "O processo auxiliar 32-bit do terminal {L} não iniciou (código {n}). {fim_do_boot}" |
| `WorkerCrashed` | | | `unknown` em op N | "O processo auxiliar 32-bit do terminal {L} parou." + (op N) " Não sei se a entrada chegou ao host." |
| `WorkerHung` | | | idem | "O RUMBA não respondeu a {funcao} em {s:.0f} s; o processo auxiliar foi reiniciado." + idem |
| `ProtocolError` | | | | "Mensagem inválida no canal do terminal {L} ({detalhe})." |
| `ClientTableFull` | | | | "O RUMBA esgotou a tabela de 32 clientes HLLAPI." Dica: "feche as janelas do RUMBA e os scripts; se persistir, faça logoff (ou reinicie, se a máquina for compartilhada)." |
| `ChannelHijacked` | | | | "Outro processo (PID {pid}) ocupa o canal do terminal {L}; não vou usá-lo." |
| `TerminalError` / `UnknownTerminal` | `ValueError` | | | "Terminal '{x}' desconhecido. Use D, Z ou A." |
| `TerminalNotOpen` | | 1 | | "O terminal {L} não está aberto." Dica: "use rumbapy.open('{L}')." |
| `NotConnected` | | 1 | | "Sem conexão HLLAPI com {L}." Dica: "t.reconnect()." |
| `LaunchFailed` | | | | "O Host Access Web Launcher terminou com código {n} antes de registrar {L}." |
| `LaunchTimeout` | `TimeoutError` | | | "O {perfil} não ficou pronto em {s:.0f} s: {fase}." (`fase` ∈ "a letra não foi registrada" / "a conexão com o host não subiu (verifique VPN/rede)" / "a conexão subiu, mas o host não mandou a primeira tela") |
| `MultipleInstances` | | | | (attach) "Há {n} janelas {perfil} abertas (PIDs {pids})." Dica: "rumbapy.open('{L}') fecha todas." / (open) "Outra janela que não consegui fechar usa a letra {L}." Dica: "feche-a (aba do Edge no modo IE?) e tente de novo." (I38) |
| `InstanceNotClosable` | | | | "A letra {L} continua registrada por um processo que não consigo fechar ({imagem}, PID {pid})." |
| `TerminalInUse` | | 11 | | (lease) "O terminal {L} está em uso por outro script (PID {pid}, {script}, desde {hora})." Dica: "rumbapy.open('{L}') ou rumbapy.close('{L}') fecham e liberam." / (rc 11) "Outro programa HLLAPI está conectado ao {L}{extra}." |
| `CloseFailed` | | | | "Não consegui fechar {n} janela(s) do {perfil}: {lista sanitizada}." Dica: "feche-as pelo X." |
| `SessionStopped` | | 12 | `unknown` em op N | "A sessão do emulador {L} foi encerrada (janela fechada?)." |
| `HostConnectionLost` | | 36 | idem | "A conexão com o mainframe caiu ({L})." Dica: "rumbapy.open('{L}') de novo." |
| `SessionReplaced` | | | `none` | "Este objeto se refere a uma instância do {L} que foi fechada/reaberta (por este ou outro script). Use a Session nova." |
| `SessionUncertain` | | | `none` | "A última entrada pode ter chegado ao host; confirme a tela com wait_text/wait_any antes de digitar (ou t.acknowledge())." |
| `HllapiError` / `ParameterError` | `ValueError` | 2 | `none` | "{detalhe}" |
| `HostBusy` | | 4 | `none` se 1 tecla, senão `partial` | "O host está ocupado (X SYSTEM); parte das teclas pode ter sido digitada." |
| `KeyboardLocked` | | 5 | idem | ver exemplo acima |
| `Truncated` | | 6 | `none` (pré-checagem) / `partial` (rc 6 da DLL) | "O texto ({n}) não cabe no campo ({m}) em ({r},{c})." — `secret`: "O valor secreto é maior que o campo em ({r},{c})." |
| `ProtectedField` | | | `none` | "({r},{c}) é um campo protegido; não dá para escrever." |
| `InvalidPosition` | `IndexError` | 7 | `none` | "({r},{c}) está fora da tela {rows}x{cols}." |
| `UndefinedKey` | `ValueError` | 20 | `none` | "Tecla '{x}' desconhecida. {sugestao}" |
| `FieldNotFound` | | 24 | `none` | "Não há campo em ({r},{c}) (tela não formatada?)." |
| `HllapiSystemError` | | 9 | `unknown` em op N | "Erro interno do RUMBA em {funcao} (rc 9)." |
| `GuardViolation` | | | `none` | "Bloqueado pelo intertravamento de teste ({politica}): {detalhe}." |
| `WaitTimeout` | `TimeoutError` | | | ver G.4 |
| `└ NoHostResponse` | | | `unknown` | "A tecla {tecla} foi enviada, mas o host não respondeu em {s:.0f} s." |
| `EncodingError` | `ValueError` | | `none` | "O caractere '{ch}' (U+{cp:04X}) na posição {i} não existe no terminal." — `secret`: "O valor secreto contém um caractere não suportado." |
| `RecipeError` / `RecipeNotAvailable` | | | | "t.{receita} só existe em terminais {tipo} ({letras})." |
| `UnexpectedScreen` | | | | "Esperava {tela}; a linha 1 atual é: '{linha1}'." Dica: "use rumbapy.open('{L}') para começar de uma tela limpa." |
| `LogonFailed` / `PasswordExpired` / `InvalidPassword` / `HostError` | | | | "O logon não terminou: {motivo}." / "A senha do CICS está expirada. Troque-a manualmente no terminal." / "Usuário ou senha do CICS incorretos." / "O CICS mostrou a tela 'E R R O   G R A V E'." |

Regras: mensagens nascem onde o erro nasce (worker para rc da DLL); nunca citam credenciais; o worker nunca põe
conteúdo de tela em mensagem; textos de janelas passam por `_instances.sanitize()` (troca URLs, FQDNs, IPv4 e o que vem
depois de `Address:` por `<host>`, C22/R33). O cliente anexa `screen` só a `WaitTimeout`, `NoHostResponse`,
`UnexpectedScreen`, `HostError`, `LogonFailed` e só se `not screen.hidden_unknown`; dentro de `cics/ims.logon`, entre a
escrita da senha e a resposta do host, `screen=None` (I15). `delivery` é definido pelo **worker** em `send`/`write`/
`set_cursor` (regra exata em D.5) e pelo cliente para falhas de transporte em op N (`unknown`).

### B.8 Receitas (`recipes/cics.py`, `recipes/ims.py`)

Receitas são código comum sobre `Session`. Toda escrita de senha usa `fill(..., secret=True)`; antes de escrever a
senha, a receita confere que o campo de destino é **oculto** (`Field.hidden`), senão `UnexpectedScreen` sem escrever (C11).

`wait_any` aceita, além de `str`/`re.Pattern`/`(texto, row, col)`, um `Callable[[Screen], bool]`.

```python
class Cics:   # perfis kind="cics"
    MENU   = ("Select CICS Environment", 14)        # texto na linha 14 (validado ao vivo: começa em (14,26))
    MENU_FIELD = (22, 56)                           # desprotegido (validado ao vivo)
    SIGNON = ("TERMINAL", 4, 6); USER = (12, 21); PASSWORD = (13, 21)   # [a validar no L6, lendo]
    OK = ("Signon", 5, 20); MSG = (19, 19)          # [a validar no L6]
    def logon(self, user: str, password: str, *, environment: str = "C", timeout: float | None = None) -> Screen
    def check(self, screen: Screen) -> None         # HostError se screen.get(5, 31, 19) == "E R R O   G R A V E"
    def start_transaction(self, code: str, *, row: int = 1, col: int = 2,
                          wait_for: tuple[str, int, int] | None = None) -> Screen   # write + enter [+ wait_text]
```
Fluxo de `Cics.logon` (cada passo com o prazo `timeout` ou `t.timeout`):

| # | Ação | Decisão |
|---|---|---|
| 1 | `s = snapshot(fields=True)` | MENU (texto na linha 14 **e** campo (22,56) desprotegido) → 2; SIGNON (texto em (4,6) **e** (12,21) desprotegido **e** (13,21) desprotegido **e oculto**) → 3; outra → `UnexpectedScreen` |
| 2 | `fill(22,56, environment)`; `enter()`; `wait_text("TERMINAL", 4, 6)`; `s = snapshot(fields=True)` | não é SIGNON completo → `UnexpectedScreen` |
| 3 | `fill(12,21,user)`; `fill(13,21,password, secret=True)`; `enter()` | falha **antes** do ENTER → `fill(13,21,"",secret=True)` (melhor esforço) e relança com `screen=None` |
| 4 | `wait_any({"expirada": ("SENHA EXPIRADA",19,19), "invalida": ("SENHA INVALIDA",19,19), "ok": lambda s: s.get(5,20,6) == "Signon" and s.get(4,6,8) != "TERMINAL"})` | erros testados **antes** de `ok` (C11); `expirada` → `PasswordExpired`; `invalida` → `InvalidPassword`; `ok` → devolve o Screen |

`Ims.logon(user, password, *, app="RHELP", uss_command="IMSRS", timeout=None) -> Screen` (fluxo da v0.1.5, âncoras
**[a validar no L7, que exige autorização]**): `wait_text("USS010",2,1)` → `write(5,1,uss_command)`+`enter()` →
`wait_text("Welcome",4,44)` → `fill(10,47,user)`, `fill(11,47,password,secret=True)` (campo oculto conferido) + `enter()` →
`wait_text("Start",16,20)` (timeout → `LogonFailed` com `screen`) → `fill(16,47,app)` + `enter()`.
O detector "E R R O   G R A V E" só existe em perfis CICS.

### B.9 Compatibilidade

#### B.9.1 `RumbaClient` (`compat.py`; reexportado por `rumbapy` e `rumbapy.client`)

Assinatura idêntica: `RumbaClient(terminal_type: str, python32bit_path: Path | str | None = None, auto_update: bool = True)`.
Atributos: `terminal_type`, `python_32bit` (Path, resolvido no `__init__` por `_runtime32.find`), `session_id`
(`f"{L}_{uuid4().hex}"`), `connected`, `HOST="localhost"`, `PORT` (8500/8600/8700/8800, **sem uso**). O worker só sobe na
1ª op. Construtor interno (testes): `RumbaClient(..., _session_factory=None)`.

Regra de erro (C28): toda exceção que sai de um método compat é a exceção v2 original (tipo preservado, todas
`RuntimeError`), com `message` prefixada por `Comando '<acao>' falhou: ` (texto exato da v0.1.5); para logon, o resto
começa com o texto legado: `SENHA DO CICS ESTÁ EXPIRADA` / `SENHA DO CICS ESTÁ INCORRETA`.

| Método | Implementação v2 | Retorno | Diferença registrada (CHANGELOG; M: Q-compat) |
|---|---|---|---|
| `__init__` | valida a letra; acha o Python 32-bit; `auto_update=False` só vale com `RUMBAPY_DEV=1` (senão WARNING "ignorado") | — | letra inválida falha na hora (`UnknownTerminal`) |
| `logon_cics(uid, pwd)` | `rumbapy.open(L)` + `t.cics.logon(uid, pwd)` | `True` | só D/Z (`RecipeNotAvailable`) |
| `logon_rhelp(uid, pwd)` | `rumbapy.open(L)` + `t.ims.logon(uid, pwd)` | `True` | só perfis IMS (`RumbaClient("A")`) |
| `send_key(key)` | nome `Mnemonic` → `Key`; senão `tokenize_mnemonic`; **corta após a 1ª AID** com WARNING "send_key: ignorado o trecho após a 1ª tecla AID (comportamento da v0.1.5)" (C3); envia como `send_keys` | `True` | rc ≠ 0 levanta (antes: silêncio) |
| `copy_ps_to_string(y, x, length)` | `read`; devolve `text[:nul_at]` se havia NUL (C.5 `read`) | `str` | acentos corretos; NUL no meio corta como antes |
| `copy_string_to_ps(y, x, text)` | `write` | `True` | `ProtectedField`/`Truncated` levantam antes de escrever |
| `copy_field(y, x)` | `read_field`; tira só os NUL finais (`nul_tail`) | `str` | tamanho real do campo (antes ≤ 80) |
| `screen_load(y, x, text, text_diff=False)` | `wait_text(text, y, x, timeout=120, gone=text_diff)` com `error_checks` do perfil | `True` | polling 50–100 ms; erro grave só em CICS |
| `close_terminal()` | `rumbapy.close(L)` (também com a sessão `replaced`, que é descartada; A.11 f) | `True` | fecha só janelas RUMBA do perfil, de qualquer script |
| `disconnect()` | `Session.disconnect()`; sem sessão: nada | `True` | — |
| `connect()` | sem Session (ou `closed`/`replaced`): `rumbapy.attach(L, timeout=60)`; senão `reconnect()` | `True` | sem terminal → `TerminalNotOpen` (antes: `True` falso) |

Detalhes (A.11 e–g): `connected` só vira `True` depois do sucesso de `logon_*`/`connect`/anexo automático e acompanha
`Session.connected`; `python32bit_path` falso (`""`) = descoberta automática; `copy_string_to_ps(y, x, "")` não faz
nada e devolve `True`.

Uso preguiçoso: qualquer op sem `logon_*`/`connect` faz `attach(L, timeout=10)` automaticamente.

#### B.9.2 `rumbapy.client` (módulo legado)
Exporta `RumbaClient`, `Mnemonic` (mesmo `Literal` da v0.1.5), `RUMBAPY_REPO`, e:
`update_python_64bit()` → `_update.ensure_latest(context="legacy", force=True)` (instala se houver versão nova e
levanta `UpdateRequired`; nunca reexecuta, I6); `ensure_python_32bit(path)`/`update_python_32bit(path)` → validam o
runtime (I.1), não instalam nada (log INFO); `find_python_32bit()` → `_runtime32.find()` (levanta `Python32NotFound`, que é `ValueError`).

#### B.9.3 `rumbapy.api`
Stub: `raise ImportError("rumbapy.api foi removido na v2; use rumbapy.open(...) ou RumbaClient")`.

#### B.9.4 `rumbapy.server` (stub para clientes 0.1.5 em memória; I25, C13, R18)
~40 linhas, só stdlib, **sem DLL**: aceita `--terminal_type`, escuta `127.0.0.1:<porta da letra>`, uma conexão por
requisição (lê até EOF), responde `ping` → `{"success": true, "message": "Server is running", "shim": true, "version": "<v>"}`
e qualquer outra ação → `{"success": false, "error": "O RumbaPy foi atualizado para a v2. Rode o script de novo."}`.
Nunca registra `params`. Sai após 120 s ou após a 1ª ação que não seja `ping`.

### B.10 Exemplos do README

```python
# CICS em lote — padrão seguro: confirme a tela depois de cada AID e PARE no primeiro erro
import re, getpass, pandas as pd, rumbapy
df = pd.read_csv("custos.csv", sep=";", dtype=str).dropna(subset=["PN"])
resultado = []
with rumbapy.open("D", window="minimized") as t:
    t.cics.logon("A000000", getpass.getpass("Senha CICS: "))
    t.cics.start_transaction("SPRE", wait_for=("VOLVO", 1, 2))
    for row in df.itertuples():                                 # coordenadas ilustrativas
        try:
            t.fill(3, 14, row.PN).enter()
            t.wait_text(row.PN, 3, 14)                          # a tela é do PN certo?
            t.fill(14, 45, row.CUSTO, align="right").enter()
            tela = t.wait_any({"ok": ("ATUALIZADO", 23, 7), "erro": re.compile(r"INVALID|ERRO")})
            resultado.append({"PN": row.PN, "STATUS": tela.match, "MSG": tela.get(23, 7, 74).strip()})
        except rumbapy.RumbaError as e:
            resultado.append({"PN": row.PN, "STATUS": f"Falha: {e.message}"})
            break                                               # NUNCA siga para o próximo PN numa tela não confirmada
pd.DataFrame(resultado).to_excel("resultado.xlsx", index=False)
```
Outros exemplos: compat sem alterações (`RumbaClient("D").logon_cics(...)`); IMS (`rumbapy.open("A")` +
`t.ims.logon`); lista paginada (`tela.table(...)` + `t.pf(8)` + `t.wait_change(since=tela)`); Jupyter (`t = rumbapy.attach("D")`,
`t.screen`, `t.screen.input_fields`, e **`t.release()` ao terminar**).

### B.11 Prazos padrão (todos sobrescrevíveis por argumento)

| Item | Padrão | Ambiente |
|---|---|---|
| `open` total | 90 s | `RUMBAPY_OPEN_TIMEOUT` |
| `attach` / `reconnect` | 10 s | — |
| esperas (`wait_*`, pré-tecla, pós-AID) | 30 s (`Session.timeout`) | `RUMBAPY_TIMEOUT` |
| `close` | 15 s | — |
| `RumbaClient.screen_load` | 120 s | — |
| polling | 50 ms no 1º segundo, depois 100 ms | — |

---

## C. Protocolo cliente 64-bit ↔ worker 32-bit ("protocolo 2", `_ipc.py` + `_protocol.py`)

### C.1 Transporte (`_ipc.py`, usado pelos dois lados)

| Item | Contrato |
|---|---|
| Canal | named pipe em modo mensagem, um por worker, nome `\\.\pipe\rumbapy2-<tag>-<L>-<rand>` (A.5). O nome sai do registro do worker; o cliente nunca o deduz |
| Criação (worker) | `_winapi.CreateNamedPipe(name, PIPE_ACCESS_DUPLEX \| FILE_FLAG_OVERLAPPED [\| FILE_FLAG_FIRST_PIPE_INSTANCE na 1ª instância], PIPE_TYPE_MESSAGE \| PIPE_READMODE_MESSAGE \| PIPE_WAIT, PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, addressof(SECURITY_ATTRIBUTES))` com DACL de `ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;GA;;;<SID do usuário>)(A;;GA;;;SY)")` (R3). Handle embrulhado em `multiprocessing.connection.PipeConnection`. **Validado localmente em 2026-10-03** (3.13 x64 e x86, DACL + `PipeConnection`). Falha ao montar a DACL → saída 18 |
| Aceitação (worker) | T-accept mantém 1 instância pendente: `ConnectNamedPipe(h, overlapped=True)`, espera o evento em fatias de 0,5 s (checando parada); conectou → entrega a uma thread T-conn nova e cria a próxima instância. Máx. **8** conexões vivas; excedente é fechado na hora |
| Conexão (cliente) | `ipc.connect(name, expected_pid, timeout=5.0)`: laço `_winapi.WaitNamedPipe(name, 200)` (erro 2 → `PipeNotFound`; 121 → repete) + `CreateFile(GENERIC_READ\|GENERIC_WRITE, OPEN_EXISTING, FILE_FLAG_OVERLAPPED)` (231 `PIPE_BUSY` → repete) + `SetNamedPipeHandleState(PIPE_READMODE_MESSAGE)`; depois `GetNamedPipeServerProcessId(h) == expected_pid`, senão `ChannelHijacked` (R3c, C27). Nunca usar `multiprocessing.connection.Client`/`Listener` (R2) |
| Mensagens | só `send_bytes`/`recv_bytes`; JSON UTF-8 `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`; pickle proibido |
| Limite | `MAX_MESSAGE = 1_048_576`; toda leitura é `poll(prazo)` + `recv_bytes(maxlength=MAX_MESSAGE)`; excesso → `ProtocolError` e fecha |

### C.2 Autenticação mútua (HMAC-SHA256 própria, com prazos)

```
W→C {"t":"auth","v":1,"nw":"<64 hex>"}                                  # nw = secrets.token_bytes(32)
C→W {"t":"auth","nc":"<64 hex>","mc":hex(hmac(K, b"rumbapy2|c|" + nw + nc))}
W→C {"t":"auth_ok","mw":hex(hmac(K, b"rumbapy2|w|" + nc + nw))}          # mc inválido => worker fecha (WARNING + PID do cliente)
```
Cada passo tem prazo `AUTH_STEP_S = 2.0` (`poll`), nos dois lados. `compare_digest` sempre. `mw` inválido no cliente →
`ChannelHijacked` (erro final, sem reciclar, sem sinalizar evento). Chave `K` = `authkey.bin` (32 bytes):
* criação "se não existir" (R12): `os.open(tmp, O_CREAT|O_EXCL|O_WRONLY|O_BINARY)` + escrita + `os.rename(tmp, final)`;
  `FileExistsError` → apaga o `tmp` e lê a existente. Nunca `os.replace` na chave;
* tamanho ≠ 32 → recriar só sob `locks\key.lock` **e** sem nenhum registro de worker vivo; senão
  `WorkerError("authkey.bin está corrompido e há processos auxiliares vivos; feche os scripts e rode de novo")`;
* o worker lê por `--keyfile`; saída 13 → o cliente relê e tenta 1× (nunca recria por causa do 13).

### C.3 Handshake de sessão e formato das mensagens

```jsonc
// W→C (logo após auth_ok)
{"t":"hello","protocol":2,"version":"2.0.0","build_id":"9f2c1e0b7a4d3c21","pid":4321,"letter":"D",
 "python":"3.13.2","started_at":"2026-10-03T14:00:00-03:00","state":"listening"|"owned"|"stopping",
 "owner":{"pid":5555,"script":"lote.py","since":"…"}|null,"connected":true,"hwnd":1234567|null,"guard":"none"}
// C→W (prazo 3 s)
{"t":"hello_ack","mode":"owner"|"admin","client_pid":9876,"client_version":"2.0.0","build_id":"…","script":"custo.py"}
// W→C
{"t":"welcome","mode":"owner"} | {"t":"reject","reason":"busy"|"build"|"stopping","owner":{…}|null}
```
Regras do worker: `mode=owner` → `stopping` ⇒ reject `stopping`; já há dono ⇒ reject `busy`; `build_id` ≠ o seu ⇒
reject `build`; senão concede o lease (`owner = {pid, script, since}`, registro atualizado). `mode=admin` → sempre `welcome`
(salvo `stopping`), sem lease.

**Ordem de decisão do cliente** (`_bridge.handshake`; resolve R1/E2 — o dono vem antes do build):
1. `hello.protocol != 2` → `> 2`: `UpdateRequired`; senão `ProtocolError`.
2. `hello.state == "stopping"` → fecha, espera o PID sair (≤ 10 s, em fatias) e volta ao `get_worker` (conta como tentativa).
3. `mode=owner` e `hello.owner` não nulo e `owner.pid != os.getpid()` → fecha e `TerminalInUse` (**nunca** para esse worker).
4. `mode=owner` e `hello.build_id != BUILD_ID` (em `mode=admin` o build não importa: `ping/sessions/evict/shutdown` são o
   contrato estável entre versões) → `disk = _buildid.compute(pkgdir)`: `disk != BUILD_ID` → `UpdateRequired("Outro processo
   atualizou o RumbaPy. Rode de novo.")`; `disk == BUILD_ID` (worker velho e **sem dono**) → conexão admin → `shutdown{"reason":"build"}`
   → espera sair → spawn.
5. Senão envia `hello_ack`.

Requisição / respostas / eventos:
```jsonc
{"id":17,"op":"read","args":{"row":23,"col":7,"length":74},"hwnd":1234567}   // hwnd = identidade da instância (C.6)
{"id":17,"ok":true,"result":{"text":"…","busy":false,"nul_at":-1,"nul_tail":0},"ms":1.3}
{"id":17,"ok":false,"error":{"type":"KeyboardLocked","message":"…","hint":"…","rc":5,"function":"WD_SendKey",
                             "delivery":"partial","details":{"chunks_sent":1}}}
{"event":"evicted","reason":"close"} | {"event":"shutting_down","reason":"idle"|"stop_event"|"watchdog"|"shutdown"|"build"}
```
* `id` inteiro crescente por conexão (começa em 1); uma requisição em voo por conexão (cliente síncrono); o worker
  responde na ordem. Resposta com `id` diferente do esperado → `ProtocolError`.
* Eventos (sem `id`) podem chegar a qualquer momento: o cliente os consome ao ler (log INFO). `evicted` → handle marcado
  como despejado (toda op atual/futura → `SessionReplaced`); `shutting_down` → handle morto; a requisição em curso vira
  `WorkerCrashed` (C.8).
* Texto no wire (`text` de `write`, `chunks` de `send`, resultados) = `bytes_cp1252.decode("cp1252")`; o worker reencoda
  com `"cp1252"` estrito (falha → `ParameterError`). O cliente já validou com `_codec.encode` (F.4). OIA = hex de 103 bytes.
  Posições em resultados = `[row, col]`.

### C.4 Tabela de operações (`_protocol.OPS`, código; I1)

```python
@dataclass(frozen=True)
class Op:
    name: str
    args: Mapping[str, type | tuple[type, ...]]   # nomes e tipos exatos (bool não vale como int)
    mode: Literal["owner", "admin", "both"]
    idempotent: bool          # I = repetível 1× após reciclar
    needs_connection: bool    # NotConnected se o worker não estiver conectado
    checks_hwnd: bool         # compara req["hwnd"] com o HWND conectado (SessionReplaced)
    client_timeout: float     # prazo do cliente (> limite do watchdog de qualquer chamada da op + 5 s)
OPS: Mapping[str, Op]
```

| op | mode | args | result | I | conn | hwnd | prazo cliente | Chamadas WD_* |
|---|---|---|---|---|---|---|---|---|
| `ping` | both | — | `{uptime_s, letter, state, connected, owner_pid, hwnd}` | I | não | não | 5 | — |
| `sessions` | both | — | `{sessions: [SessionEntry]}` | I | não | não | 25 | `QuerySessions` |
| `connect` | owner | `{letter}` | `ConnectResult` | I | não | não | 25 | `ConnectPS` (se desconectado), `SetSessionParamEx("NORESET")`, `QuerySessionStatus`, `GetSessionHWND` |
| `disconnect` | owner | — | `{}` | I | não | não | 25 | `DisconnectPS` |
| `status` | owner | — | `SessionStatus` | I | sim | sim | 25 | `QuerySessionStatus` |
| `snapshot` | owner | `{fields: bool}` | `{text, cursor, oia, busy, hidden_unknown, fields?: [{start, length, attr}]}` | I | sim | sim | 25 (90 c/ fields) | `CopyPS`, `QueryCursorLocation`, `CopyOIA` (+ enumeração D.6) |
| `read` | owner | `{row, col, length}` | `{text, busy, nul_at, nul_tail}` | I | sim | sim | 25 | `CopyPSToString` |
| `read_field` | owner | `{row, col}` | `{text, start, length, attr, nul_tail}` | I | sim | sim | 25 | `FindFieldPosition("T ")`, `FindFieldLength("T ")`, `QueryFieldAttribute`, `CopyFieldToString` |
| `write` | owner | `{row, col, text, secret}` | `{}` | N | sim | sim | 45 | `QueryFieldAttribute`, `FindField*("T ")`, `CopyStringToPS` |
| `send` | owner | `{chunks: [str], secret}` | `{chunks_sent, oia_after}` | N | sim | sim | 25·n + 5 | `SendKey` × n, `CopyOIA` (logo após o último) |
| `set_cursor` | owner | `{row, col}` | `{}` | N | sim | sim | 25 | `SetCursor` |
| `evict` | admin | `{reason}` | `{evicted_owner: {pid, script} \| null, was_connected}` | — | não | não | 25 | `DisconnectPS` |
| `shutdown` | both | `{reason}` | `{}` (o worker sai) | — | não | não | 30 | `DisconnectPS`, `ResetSystem` |

`ConnectResult = {connected: bool, rc: int, already: bool, rows, cols, host_cp, long_name, type, eab, hwnd, hwnd_pid}`
(com `connected=false` só `rc` vale). `nul_at` = índice do 1º byte NUL no bruto (−1 se nenhum); `nul_tail` = nº de NUL
finais (usados só pela compat, B.9.1). Cortadas (I27, I22): `search`, `cursor`, `oia`, `field_attr`, `write_field`,
`wait_kb`, `cancel`, `open`, `close`, `instances`, `window`, `diag`, `attach`.

Validações no worker **antes** da DLL (`dispatch.validate`): tipos exatos; argumento desconhecido/ausente →
`ParameterError`; `1 ≤ row ≤ rows`, `1 ≤ col ≤ cols` → senão `InvalidPosition`; `length ≥ 1` e `pos+length-1 ≤ rows*cols`;
`write.text` não vazio; `send`: `1 ≤ len(chunks) ≤ 64`, cada chunk ≤ 128 bytes cp1252, tokenizável por
`keys.tokenize_mnemonic`; **AID só como conteúdo inteiro do último chunk** (um único mnemônico de `keys.AIDS`).

### C.5 Interface `Backend` (`_backend.py`; espelho 1:1 de `OPS`, I1)

```python
class Backend(Protocol):
    letter: str
    def ping(self) -> dict
    def sessions(self) -> list[SessionEntry]
    def connect(self) -> ConnectResult                 # UMA tentativa; o laço é do chamador (_launcher)
    def disconnect(self) -> None
    def status(self) -> SessionStatus
    def snapshot(self, *, fields: bool = False) -> Screen
    def read(self, row: int, col: int, length: int) -> ReadResult            # (text, busy, nul_at, nul_tail)
    def read_field(self, row: int, col: int) -> FieldRead                    # (text, start, length, attr, nul_tail)
    def write(self, row: int, col: int, text: str, *, secret: bool) -> None  # text já no formato de wire
    def send(self, chunks: Sequence[str], *, secret: bool) -> SendResult     # (chunks_sent, oia_after: OIA)
    def set_cursor(self, row: int, col: int) -> None
    def evict(self, reason: str) -> dict               # conexão admin própria
    def shutdown(self, reason: str) -> None
    # só do cliente (fora de OPS): 
    def bind_instance(self, hwnd: int | None) -> None  # hwnd enviado em toda op com checks_hwnd
    def release(self) -> None                          # fecha a conexão de lease (não é op)
```
Implementações: `WorkerBackend` (`_backend.py`, P-D) e `InProcessBackend` (`tests/fakes/fake_backend.py`, P-B: chama o
`Dispatcher` do worker no próprio processo sobre `FakeEhllapi`, passando cada mensagem por `json.dumps/loads`).
`tests/unit/test_contracts.py`: nomes e argumentos de `Backend` (menos `bind_instance`/`release`) == chaves de `OPS` ==
`Dispatcher.HANDLERS`, e `WorkerBackend`/`InProcessBackend` implementam todos. Toda implementação levanta só `RumbaError`.

### C.6 Lease, instância e concorrência

1. **Um dono por worker.** O lease nasce no `hello_ack mode=owner` e termina no EOF da conexão (inclui `release()`,
   morte do processo, Ctrl+C), ou no `evict`.
2. **`evict`** (só por conexão admin; usado por `close_all`, E.4): vira job na fila de T-dll (após o job atual);
   `DisconnectPS` se conectado; a conexão do dono é marcada `evicted` e recebe `{"event":"evicted"}`; `owner = None`;
   registro atualizado. Toda op posterior dessa conexão → `SessionReplaced`. É assim que `open`/`close` fecham
   **qualquer** instância sem esperar o outro script (pedido do dono; R5, C17, I10).
3. **`shutdown`** e o **evento de parada** só são honrados com `owner is None`; com dono: `shutdown` responde
   `TerminalInUse` e o evento é registrado (WARNING) e adiado até o fim do lease (R1b).
4. **Fim do lease**: T-conn sinaliza `cancel` de todos os jobs daquela conexão (em voo: `send` para antes do próximo
   chunk; na fila: descartados com log `dropped id=… reason=owner_gone`, R20); depois, se `_buildid.compute(pkgdir) !=`
   o próprio, o worker encerra limpo (`reason=build`, E2).
5. **Identidade da instância**: o resultado de `connect` traz `hwnd` (`WD_GetSessionHWND`); a `Session` faz
   `backend.bind_instance(hwnd)` e toda op com `checks_hwnd` leva `"hwnd"`; se o worker estiver conectado a outro HWND (ou
   desconectado por `evict`), responde `SessionReplaced` (C12, I45). Uma 2ª `open()` no mesmo processo marca as `Session`
   anteriores da letra como `replaced` também no cliente.
6. No processo cliente, `_bridge` mantém `dict[letra, WorkerHandle]`; as `Session` da letra compartilham o handle; um
   `threading.Lock` por handle serializa as requisições (Session segura entre threads, não paralela); contagem de
   `Session` conectadas decide quando `release()` fecha a conexão.

### C.7 Prazos e Ctrl+C no cliente

* Espera de resposta: laço `poll(0.1)` até `OPS[op].client_timeout`; a cada 1 s confere se o PID do worker vive
  (`WaitForSingleObject(h, 0)`).
* **Ctrl+C** durante uma op: o cliente **fecha a conexão** (o worker cancela os jobs dela, C.6.4), marca a `Session`
  `uncertain` se a op for N, e relança `KeyboardInterrupt`. A próxima op reabre a conexão (handshake ~1 ms) e retoma o lease.
* Estourou `client_timeout`: fecha a conexão; trata como `WorkerHung` (C.8): espera o PID sair até 30 s (em fatias);
  se não sair, `WorkerError("O processo auxiliar do terminal {L} (PID {pid}) não encerrou.")` com a dica "feche as janelas do
  RUMBA; se persistir, faça logoff (não use o Gerenciador de Tarefas)". **Nunca** `TerminateProcess`.

### C.8 Erros de transporte e recuperação

O `try` envolve **só** `send_bytes`/`recv_bytes`/`poll` e captura `(EOFError, BrokenPipeError, ConnectionError)` e `OSError`
cujo tipo não seja subclasse de `RumbaError` (E23).

| Situação | Classe | Ação |
|---|---|---|
| EOF/pipe quebrado/`OSError` de transporte | `WorkerCrashed` | lê `exit_reason` do registro; op I: reciclar e repetir 1×; op N: levantar com `delivery="unknown"` (Session `uncertain`) |
| `error.type == "WorkerHung"` | `WorkerHung` | idem |
| sem resposta em `client_timeout` | `WorkerHung` | fechar conexão, esperar o PID sair, reciclar; op I repete 1× |
| `mw` inválido ou PID do servidor do pipe ≠ registro | `ChannelHijacked` | erro final; não recicla; não sinaliza o evento |
| JSON inválido / `id` inesperado / > 1 MiB | `ProtocolError` | fechar; reciclar; op I repete 1× |

**Reciclar** = `get_worker` (D.8) → handshake owner → `connect(letter)` em laço por até 15 s tolerando rc 1 **e rc 11**
(R8: conexão do worker anterior presa após `os._exit`) → `hwnd` diferente do da `Session` → `SessionReplaced`; igual →
repete a op I. rc 11 por 15 s → `TerminalInUse` com o extra " (a conexão do processo auxiliar anterior pode ter ficado presa;
use rumbapy.open('{L}'), que fecha e reabre)". Log: `retry op=read after=WorkerHung`. Nenhuma op N é repetida.

---

## D. Worker 32-bit (`_worker/*`)

Um processo Python 32-bit **por letra e por sessão do Windows**, dono exclusivo da conexão HLLAPI daquela letra,
executado a partir dos arquivos do pacote 64-bit, só stdlib, uma única thread de DLL, sem `MultiThreadConnectionOn`.

### D.1 Linha de comando e BOOT (montados por `_bridge.spawn`)

```
<python32 real> -I -S -X faulthandler -c <BOOT> <pkgdir>
    --letter D --pipe <nome> --keyfile <HOME>\authkey.bin --stop-event Local\rumbapy2-<tag>-D-stop
    --registry <HOME>\run\worker-<tag>-D.json --log <HOME>\logs\worker-<tag>-D.log --log-level INFO
    --idle 1800 --expect-build <BUILD_ID> --commit <sha|none>
    --dll "C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"
    [--guard none|readonly|text|login]  [--fake-dll <modulo>:<fabrica> --fake-path <dir>]  [--allow-64]
```
`BOOT` (constante `_bridge.BOOT`, uma linha, **sem barra invertida**, E19; não executa `rumbapy/__init__.py`, I12):
```python
import os,sys,types; d=sys.argv[1]; m=types.ModuleType("rumbapy"); m.__path__=[d]; m.__file__=os.path.join(d,"__init__.py"); sys.modules["rumbapy"]=m; from rumbapy._worker.main import main; sys.exit(main(sys.argv[2:]))
```
Validado localmente em 2026-10-03 (3.13 x64 e x86, `-I -S -W error::SyntaxWarning`): `__init__` não roda, imports
relativos funcionam, `sys.path` sem `site-packages`. "Python 32 real" = `real_executable` de `_runtime32` (I.1, E14).

`Popen`: `env` = `os.environ` sem chaves `PYTHON*` e `RUMBAPY_*`, mais `RUMBAPY_SANDBOX`/`RUMBAPY_LIVE`/`RUMBAPY_HOME`
se definidas; `stdin`/`stdout` = `DEVNULL`; `stderr` = arquivo `logs\boot\…txt` novo (A.5); `cwd = HOME`; `close_fds=True`;
`creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` (se `WinError 5`: repete sem
breakaway e loga WARNING "o processo auxiliar pode ser morto à força quando a IDE fechar"). `--fake-dll` só é aceito com
`RUMBAPY_SANDBOX=1`; `--fake-path` só junto com `--fake-dll` (é inserido em `sys.path[0]`); `--allow-64` só com `--fake-dll`.

### D.2 Ordem de boot (`main`) e códigos de saída

1. `struct.calcsize("P") == 4` (salvo `--allow-64`) e `sys.version_info >= (3, 12)`, senão **12**.
2. Logging (D.7); linha `start pid=… python=… letter=D guard=…`.
3. Importa **todos** os módulos de `_worker` e calcula `own = _buildid.compute(pkgdir)`; `own != --expect-build` → **14** (E13, R11c).
4. Lê a chave (32 bytes) → senão **13**.
5. Cria a 1ª instância do pipe com `FILE_FLAG_FIRST_PIPE_INSTANCE` → já existe → **11**; DACL falhou → **18**
   (o pipe é reservado **antes** de carregar a DLL, E20/I37).
6. Colisão de identidade HLLAPI (R10): `pids = _win32.hllapi_client_pids()`; `collides(own_pid, pids)` =
   `any(p != own_pid and (p & 0xFFFF) == (own_pid & 0xFFFF) for p in pids)` → **15**.
7. `RUMBAPY_SANDBOX=1` sem `--fake-dll` → **17**; `RUMBAPY_LIVE=1` com `--guard none` → **16**.
8. Sobe T-dll, que carrega a DLL (`hllapi.load_dll`, F.1) e a embrulha em `GuardedDll` se `--guard != none` → falha → **10**.
9. Abre/cria o evento de parada (se já sinalizado, `ResetEvent`). Grava o registro (D.7) com `state="listening"`.
10. Sobe T-watch e T-accept → `LISTENING`.

| Código | Significado | O cliente traduz para |
|---|---|---|
| 0 | parada limpa (`shutdown`, evento, fim de lease com build novo) | — |
| 1 | exceção não tratada | `WorkerStartFailed` + 20 últimas linhas do boot |
| 3 | watchdog, janela fechada durante chamada, parada com DLL presa | `WorkerHung` / `SessionStopped` (por `exit_reason`) |
| 5 | idle | — |
| 10 | DLL não carregou | `RumbaNotInstalled` |
| 11 | pipe já existe | volta ao laço do `get_worker` (D.8) |
| 12 | Python inadequado | `Python32NotFound` |
| 13 | chave inválida | relê a chave e tenta 1× |
| 14 | build divergente | `UpdateRequired` |
| 15 | colisão `LOWORD(pid)` | novo spawn (até 3 extras) |
| 16 | `RUMBAPY_LIVE=1` sem guard | `WorkerStartFailed("RUMBAPY_LIVE exige --guard")` |
| 17 | sandbox com DLL real | `SandboxViolation` |
| 18 | DACL do pipe | `WorkerStartFailed` |

### D.3 Threads e fila

| Thread | Faz | NUNCA |
|---|---|---|
| T-accept | `ConnectNamedPipe` e entrega da conexão a uma T-conn | chamar a DLL; autenticar |
| T-conn-*n* | auth (C.2), handshake (C.3), `recv` → `dispatch.validate` → enfileira `Job(id, op, args, hwnd, conn, cancel: Event)`; responde `ping` direto (leitura atômica do estado); EOF → C.6.4 | chamar a DLL |
| T-dll (única) | carrega a DLL; executa jobs em FIFO; toda chamada `WD_*` | segurar `conn.lock` durante chamada à DLL |
| T-watch | tick de 100 ms (D.4) | chamar a DLL |

* Fila global `queue.Queue(maxsize=64)`; cheia → `ParameterError("muitas requisições pendentes")`.
* Resposta: `conn.lock.acquire(timeout=1.0)` + `send_bytes`; falhou → fecha a conexão (R9a).
* Exceção Python inesperada num handler: resposta `{"type":"RumbaError","message":"Erro interno no processo auxiliar ({NomeDoTipo})."}`,
  log só com o tipo e `traceback.format_tb` (nunca `str(exc)`, C21c); se a op for N ou `connect`: `engine.resync()` (R35)
  e `details.resynced=true`.

### D.4 Watchdog (`_worker/watchdog.py`)

`Hllapi` envolve cada chamada com `inflight = (func, job_id, t0, limite)`. Limites: `WD_ConnectPS` 15 s;
`WD_DisconnectPS`/`WD_ResetSystem` no encerramento 5 s; demais 20 s.

A cada tick: (a) **suspensão** (R21): se o intervalo desde o tick anterior > 5 s, rearma `inflight.t0` e a base do idle;
(b) estouro de `inflight` → `fire("hung")`; (c) com `inflight` e `engine.hwnd`, `not _win32.is_window(hwnd)` em 2 ticks
seguidos → `fire("window_gone")` (R31); (d) evento de parada sinalizado e sem dono → parada limpa; com dono → WARNING
uma vez e adia; (e) idle: sem dono, fila vazia e sem `inflight` por `--idle` s → tenta `file_lock(spawn-<tag>-<L>)` sem
espera; obteve → parada limpa `reason=idle` (solta o lock depois de apagar o registro e fechar o pipe, R4); não obteve → próximo tick.

`fire(kind)` (R9) — nesta ordem:
1. inicia thread daemon `time.sleep(2.0); os._exit(3)` (**antes de qualquer outra coisa**);
2. tipo = `ClientTableFull` se `_win32.hllapi_error_dialog_visible(os.getpid())`; senão `SessionStopped` (`window_gone`)
   ou `WorkerHung`; `delivery="unknown"` se o job em voo for N;
3. responde o job em voo e envia `{"event":"shutting_down","reason":"watchdog"}` (`conn.lock` com `timeout=0.5`);
4. grava `exit_reason` no registro (orçamento 0,5 s); `flush()` de cada handler de log (sem `logging.shutdown`);
5. `os._exit(3)`. Nunca `ResetSystem` aqui; nunca `TerminateProcess`.

Parada limpa (`STOPPING`): para de aceitar; avisa o dono (`shutting_down`); `DisconnectPS` (se conectado) e
`ResetSystem` como **jobs na fila de T-dll**; se T-dll não ficar livre em 5 s → `exit_reason="stop_while_hung"` e
`os._exit(3)` (R9c); apaga o registro se o `pid` for o próprio; flush dos logs; `os._exit(0|5)`.

### D.5 `Engine` (`_worker/engine.py`) e regras de `delivery`

```python
class Engine:
    def __init__(self, hll: Hllapi, mask: MaskState) -> None
    letter: str | None; connected: bool; rows: int; cols: int; hwnd: int | None; hwnd_pid: int | None; aid_seq: int
    def sessions(self) -> list[dict]
    def connect(self, letter: str) -> dict          # ConnectResult
    def disconnect(self) -> dict
    def status(self) -> dict
    def snapshot(self, fields: bool) -> dict
    def read(self, row: int, col: int, length: int) -> dict
    def read_field(self, row: int, col: int) -> dict
    def write(self, row: int, col: int, data: bytes, secret: bool) -> dict
    def send(self, chunks: list[bytes], secret: bool, cancel: threading.Event) -> dict
    def set_cursor(self, row: int, col: int) -> dict
    def evict(self) -> dict
    def stop_calls(self) -> None                    # DisconnectPS + ResetSystem
    def resync(self) -> None                        # QuerySessions + QuerySessionStatus + GetSessionHWND; falhou => connected=False
# _worker/dispatch.py
Handler = Callable[[Engine, dict, JobCtx], dict]    # JobCtx(op, conn_id, cancel, hwnd)
class Dispatcher:
    HANDLERS: ClassVar[Mapping[str, Handler]]       # chaves == OPS (menos ping, que T-conn responde)
    def __init__(self, engine: Engine) -> None
    def handle(self, op: str, args: dict, ctx: JobCtx) -> dict   # valida (OPS), checa hwnd, chama, traduz rc
```
* `connect`: já conectado na mesma letra → `already=true`. Senão `ConnectPS`: rc 0/4/5 → conectado; então
  `SetSessionParamEx(b"NORESET")` (C8; rc ≠ 0 → WARNING e `details.noreset=false`), `QuerySessionStatus` (rows/cols),
  `GetSessionHWND` (0 → `None`) e `_win32.window_thread_pid(hwnd)`; `mask.invalidate()`. rc 1 → `{connected: false, rc: 1}`;
  rc 11 → `TerminalInUse`; rc 2 → `ParameterError`; rc 9 → `HllapiSystemError`.
* `write` (C9) — valida **antes** de copiar: `attr = QueryFieldAttribute(pos)`; rc 24 (não formatado) → limite = fim da tela;
  protegido → `ProtectedField`; oculto → `secret=True`; senão `start`/`length` por `FindFieldPosition/Length(pos, "T ")`;
  `pos == start-1` (byte de atributo) → `ProtectedField`; `len(data) > start+length-pos` → `Truncated` (`delivery="none"`).
  Depois `CopyStringToPS`: rc 0 ok; rc 5 → `KeyboardLocked` (`none`: nada copiado); rc 6 → `Truncated` (`partial`);
  rc 7 → `InvalidPosition`; rc 1 → `NotConnected`. `secret` → `mask.add_secret(pos, len)`; `mask.own_mutation()`.
* `send`: para cada chunk *i*: `cancel` sinalizado → para (sem resposta; dono foi embora); `rc = SendKey(chunk)`; rc ≠ 0 →
  exceção de F.7 com `details.chunks_sent = i` e `delivery`: rc 2/20 → `"none"` se *i* = 0, senão `"partial"`; rc 4/5 →
  `"none"` se *i* = 0 **e** o chunk for uma única unidade (1 mnemônico), senão `"partial"`; rc 1/9 → `"unknown"`.
  Após o último chunk: `oia_after = CopyOIA` (rc 0/4/5 aceitos). Último chunk = AID → `aid_seq += 1` e `mask.on_aid()`.
* `set_cursor`: rc 4 → `HostBusy` (`none`); 7 → `InvalidPosition`; 1 → `NotConnected`.
* `read`: `CopyPSToString` rc 0/4/5 → `busy = rc in (4,5)`; `nul_at`/`nul_tail` calculados no bruto; texto = `mask.apply` +
  `_codec.decode`. `read_field`: rc 24 → `FieldNotFound`; rc 28 → texto `""`; campo oculto → espaços.

### D.6 Máscara de conteúdo oculto (`_worker/mask.py`, puro; R14, C4, I15)

O conteúdo de campos não exibidos **não atravessa o pipe**. `MaskState` guarda: `hidden: list[(pos, len)] | None`
(`None` = desconhecido), `secret: list[(pos, len)]`, `enum_aid_seq`, `last_hash`, `own_mut`.
* **Enumeração** (`enumerate_fields(hll)`, compartilhada com `snapshot(fields=True)`): `start = FindFieldPosition(1, "T ")`;
  rc 24 → `[]` (PS não formatado); repetir até 1000 vezes ou até `start` repetir: `length = FindFieldLength(start, "T ")`
  (28 → 0), `attr = QueryFieldAttribute(start)`, guarda `(start, length, attr)`, `start = FindFieldPosition(start, "N ")`.
  rc inesperado → `hidden=None` e `hidden_unknown=true`.
* **Quando reenumerar** (antes de responder `snapshot`, `read`, `read_field`): cache vazio; `connect` desde a última;
  `aid_seq != enum_aid_seq` **e** teclado livre agora (o host respondeu); em `snapshot`, `sha1(PS bruto) != last_hash` **e**
  `own_mut == False` (escrita espontânea do host). Depois de `write`/`send` sem AID, `own_mut=True`: o próximo `snapshot`
  só atualiza `last_hash` e zera `own_mut`.
* **Aplicar**: bytes em `hidden ∪ secret` viram `0x20` antes do `decode`. Spans `secret` caem quando o host responde a uma
  AID (mesma regra do `aid_seq`).
* Custo: ~3 chamadas locais por campo, uma vez por tela.

### D.7 Registro e logs

Registro `run\worker-<tag>-<L>.json` (gravado com `atomic_write`; atualizado no start, mudança de lease, connect/
disconnect e parada; apagado na saída limpa se o `pid` for o próprio):
```json
{"protocol":2,"version":"2.0.0","build_id":"…","pid":4321,"created":133722334445556667,"letter":"D","pipe":"…",
 "python":"…\\python.exe","started_at":"…","log":"…","state":"listening","owner":null,"connected":true,
 "hwnd":1234567,"hwnd_pid":5678,"exit_reason":null}
```
`created` = `GetProcessTimes` (FILETIME) do worker, usado para não confundir PID reutilizado (R4f).

Logs: `SafeRotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8", delay=True)` (rollover
ignora `PermissionError`, E35). Formato dos dois lados:
`%(asctime)s.%(msecs)03d %(levelname)-5s %(process)d %(threadName)s %(message)s` (`datefmt="%Y-%m-%d %H:%M:%S"`);
mensagens do worker começam com `L=<letra>`. Ex.: `2026-10-03 14:05:12.345 INFO  4321 dll L=D op=read id=17 row=23 col=7 len=74 rc=0 ms=1.2`.
* INFO por op: nome, id, coordenadas, tamanhos (**omitidos** se `secret`), rc, duração. **Nunca** texto lido ou escrito,
  em nível nenhum (R15, C21, I15). DEBUG acrescenta `sha=<sha1(bruto)[:6]>` em leituras.
* `send`: `keys=[TAB,ENTER] text_units=<n|oculto>`. WARNING: rc tolerado, reciclagem, breakaway negado, auth falhou
  (com PID do cliente), parada adiada. CRITICAL: watchdog.
* Cliente: `logs\client-AAAAMMDD.log` (`FileHandler` append); apaga > 14 dias no 1º uso do dia ignorando `PermissionError`.

### D.8 `get_worker(letter, mode)` (cliente, `_bridge.py`; R4, E14)

```
handle em cache vivo (mode=owner) → devolve
com file_lock("spawn-<tag>-<L>", timeout=30 s, fatias de 0,1 s):          # não solta nas esperas; quem espera: rodadas de 30 s até 150 s (A.12 d)
  spawns = 0 (máx. 2; +3 só para saída 15); stops = 0 (máx. 1)
  repetir:
    reg = read_json(registro)
    se reg e process_alive(reg.pid, reg.created):
        tentar ipc.connect(reg.pipe, reg.pid, 5 s) → handshake (C.3) → ok: devolve
            stopping → espera o PID sair (≤ 10 s) → continua
            velho sem dono → stops += 1 → shutdown → espera sair (≤ 10 s) → continua
        PipeNotFound/timeout: reg.owner nulo → stops += 1 → SetEvent(stop) → espera sair (≤ 10 s);
                              ainda vivo → WorkerError("o processo auxiliar não responde"); com dono → TerminalInUse
    senão:
        spawns += 1 (> limite → WorkerStartFailed)
        _buildid.compute(pkgdir) != BUILD_ID → UpdateRequired (R11b)
        pipe = nome novo; Popen (D.1); espera até 20 s (fatias de 0,1 s):
           processo saiu → código (D.2): 11 → continua 1×; 15 → novo spawn; 13 → relê a chave, 1×; outros → exceção mapeada
           registro com pipe == o nosso e ipc.connect + hello.pid == reg.pid → handshake → devolve   # não exige Popen.pid (E14)
```
Toda espera em objeto do kernel é feita em fatias de 100 ms (Ctrl+C funciona, R2d, E27).

### D.9 Contrato de `_win32.py` (assinaturas congeladas em P-0; implementação P-B)

`ctypes.WinDLL(nome, use_last_error=True)` privados; `argtypes`/`restype` explícitos em toda função; `ctypes.get_last_error()`.
```python
def user_sid() -> str
def session_id() -> int
def process_alive(pid: int, created: int | None = None) -> bool
def process_created(pid: int) -> int | None
def process_image(pid: int) -> str | None                  # basename minúsculo
def parent_pid(pid: int) -> int | None                     # Toolhelp32
def hllapi_client_pids(dll_name: str = "ehlapi32.dll") -> list[int]   # processos com o módulo carregado
def pipe_security_attributes() -> SecurityAttributes      # mantém o SD vivo; .address() para CreateNamedPipe
def pipe_server_pid(handle: int) -> int
def pipe_client_pid(handle: int) -> int
def open_event(name: str, *, create: bool) -> int; def set_event(h: int) -> None
def reset_event(h: int) -> None; def event_is_set(h: int) -> bool
def wait_handle(h: int, timeout: float) -> bool           # fatias de 100 ms
def window_thread_pid(hwnd: int) -> int
def is_window(hwnd: int) -> bool
def show_window_async(hwnd: int, cmd: int) -> None        # sandbox: SandboxViolation
def hllapi_error_dialog_visible(pid: int | None = None) -> bool   # #32770 com título "HLLAPI ERROR"
def tcp_listener_pid(port: int) -> int | None             # GetExtendedTcpTable (E.8)
CREATE_BREAKAWAY_FROM_JOB: int; CREATE_NO_WINDOW: int; CREATE_NEW_PROCESS_GROUP: int
```

---

## E. Launcher e ciclo de vida do terminal (cliente 64-bit: `_launcher.py`, `_instances.py`, `_user32.py`, `_legacy.py`)

Requisito do dono: **abrir e fechar fecham TODAS as instâncias do perfil** (duas instâncias da mesma letra ⇒ "Short Name
already in use" e o HLLAPI fica preso à 1ª), mas **só** janelas que sejam sessões RUMBA daquele perfil.

### E.1 Perfis (`profiles.py`; `Profile` em `_types.py`)

```python
@dataclass(frozen=True)
class Profile:
    name: str; letter: str; kind: Literal["cics", "ims"]; link_text: str
PROFILES = {"D": Profile("BRAZIL_D", "D", "cics", "BRAZIL D"),
            "Z": Profile("BRAZIL_Z", "Z", "cics", "BRAZIL Z"),
            "A": Profile("IMS_A", "A", "ims", "IMS A")}
PORTAL = os.environ.get("RUMBAPY_PORTAL", PORTAL_DEFAULT)   # PORTAL_DEFAULT = base já usada pela v0.1.5, em https
def resolve(terminal: str) -> Profile                     # letra ou nome; UnknownTerminal
def profile_url(p: Profile) -> str                        # f"{PORTAL}/profpro/{p.name}.html"
def launcher_arg(p: Profile) -> str                       # f"hawlauncher://{profile_url(p)}/microfocus_w2h_hawlauncher" (1 argumento)
def index_url() -> str                                    # f"{PORTAL}/index_pro_api.html" (fallback IE)
```

### E.2 `User32` (Protocol congelado em P-0; `RealUser32` em P-C; `FakeUser32` em `tests/fakes/fake_user32.py`)

```python
@dataclass(frozen=True)
class RawWindow:
    hwnd: int; title: str; cls: str; pid: int; owner: int; visible: bool; iconic: bool
class User32(Protocol):
    def top_windows(self) -> list[RawWindow]          # EnumWindows (visíveis e invisíveis) + GetWindow(GW_OWNER)
    def process_image(self, pid: int) -> str | None   # basename minúsculo (QueryFullProcessImageNameW)
    def parent_image(self, pid: int) -> str | None
    def static_texts(self, hwnd: int) -> list[str]    # EnumChildWindows + GetWindowTextW
    def buttons(self, hwnd: int) -> list[tuple[int, str]]
    def post_close(self, hwnd: int) -> None           # PostMessageW(WM_CLOSE)
    def click(self, button_hwnd: int) -> None         # PostMessageW(BM_CLICK)
    def show(self, hwnd: int, cmd: int) -> None       # ShowWindowAsync (nunca SetForegroundWindow)
    def is_window(self, hwnd: int) -> bool
    def root_of(self, hwnd: int) -> int               # GetAncestor(GA_ROOT)
```
`RealUser32` com `RUMBAPY_SANDBOX=1`: `post_close`, `click` e `show` levantam `SandboxViolation` (E3); leituras são livres.

### E.3 Identificação de instâncias (`_instances.py`, puro)

`P = profile.name`. Uma janela de nível superior é **instância do perfil** se cumprir uma das regras:

| kind | Condições (todas) |
|---|---|
| `launcher` | `image == "host access web launcher.exe"`; `cls.startswith("WindowsForms10.")`; `title == P + " - Pro Client"` ou `title.startswith(P + " - Pro Client ")` (prefixo com espaço: `BRAZIL_DX` não casa) (R6a-ii) |
| `ie` | `image == "iexplore.exe"`; `parent_image != "msedge.exe"` (I18); `cls == "IEFrame"`; `re.fullmatch(rf"(?:.* - )?{re.escape(P)} - Pro Client(?: .*)? - Internet Explorer", title)` |
| `hllapi` | `root_of(hwnd_hllapi)` — HWND devolvido por `WD_GetSessionHWND` (registro do worker ou `ConnectResult`) — com imagem `launcher` ou `ie` acima, **qualquer título** (R6a-i) |
| `launched` | PIDs de `run\launched-<tag>-<L>.json` vivos (`created` confere) **sem** janela de instância de outro perfil: todas as suas janelas de nível superior (cobre o launcher parado num diálogo antes da janela principal, R30a) |

Janela de outro processo (ex.: `BRAZIL_D.xlsx - Excel`) nunca casa (regra de imagem). Abas do Edge não são classificadas:
só aparecem como "letra ainda registrada sem instância fechável" (E.4 passo 6).

Diálogos de uma instância = `#32770` de nível superior com `owner == hwnd da instância`; sem dono, só se o PID não tiver
instância de outro perfil (R6b). Diálogos conhecidos:

| Título | Texto contém | Durante `open` | Durante `close` |
|---|---|---|---|
| `Telnet3270 Application` | `Connecting To Address` | normal; não tocar | não tocar (o `WM_CLOSE` da janela principal basta — validado) |
| `RUMBA API Service Control` | `Short Name already in use` | outra janela segura a letra (E.5 passo 6) | `click` no botão de texto `OK` **[a validar no L2]** |
| `HLLAPI ERROR` | `Exceeded maximum number of clients` | `ClientTableFull`; **não clicar** | idem |
| outro | — | não tocar; texto sanitizado no diagnóstico | só no `close`, após 3 s: `post_close(diálogo)` (equivale a Cancelar/X); **nunca** botões afirmativos |

`sanitize(text) -> str`: `re.sub(r"(Address:\s*)\S+", r"\1<host>")`, URLs `https?://\S+` → `<url>`, FQDN
`\b[\w-]+(\.[\w-]+){2,}\b` e IPv4 → `<host>`; aplicado a todo texto de janela antes de log, exceção ou `doctor` (C22).

### E.4 `close_all(profile, *, timeout=15.0, admin: Backend | None = None) -> int`

Usado por `rumbapy.close`, `Session.close`, `close_terminal()` e `open(reuse=False)`. Não exige lease nem DLL.
```
1. Se houver worker vivo da letra (registro + process_alive): conexão admin → evict{"reason":"close"} (falha só gera WARNING).
   hwnd_hllapi = registro.hwnd.
2. inst = instâncias (E.3). notice "RumbaPy: fechando N janela(s) de <P>…" se N > 0.
3. Janela de instância invisível/oculta → show(SW_SHOWMINNOACTIVE=7) antes de fechar (não persiste estado oculto, C24).
4. post_close(hwnd) em cada janela de instância.
5. Polling de 100 ms até nenhuma is_window. Aos 3 s e aos 8 s, para cada sobrevivente: diálogos da instância (tabela E.3)
   e novo post_close na janela.
6. Verificação HLLAPI (melhor esforço; só se já houver worker ou um `admin` passado pelo open): op sessions até o prazo;
   letra ainda registrada sem nenhuma instância viva → InstanceNotClosable. Sem worker: só janelas (WARNING no log).
7. Estourou com janelas vivas → CloseFailed (títulos sanitizados). NUNCA TerminateProcess.
8. Devolve o nº de janelas de instância fechadas.
```
Validado ao vivo: `WM_CLOSE` fecha a janela do launcher em ~0,5 s sem confirmação e desregistra a letra; com duas
instâncias, fecha ambas. **[a validar no L6]**: confirmação ao fechar com sessão logada (R32).

### E.5 `open(profile, *, reuse, window, timeout=T)` (`_launcher.open_terminal`)

Referência ao vivo: registro da letra ≈ 9,4 s; `ConnectPS` rc 1 até ≈ 11,3 s; depois rc 0; logo após o rc 0 o PS pode
estar vazio (rc 4) (R16, C18). `D = t_inicio + T`.
```
0. _legacy.neutralize(L) (uma vez por processo e letra, E.8). Pré-voo: hllapi_error_dialog_visible() → ClientTableFull.
1. admin = get_worker(L, "admin") (sobe se preciso); antes = instâncias(E.3).
2. reuse=True e len(antes) == 1 → owner = get_worker(L, "owner"); laço connect por até 10 s → conectou: passos 7–9 com
   launched=False. Senão (0, 2+, ou rc 1 por 10 s) → notice e segue.
3. close_all(profile, timeout=min(15, 0.15*T), admin=admin) → closed_instances.
4. Lançar: exe = 1º token de HKCR\hawlauncher\shell\open\command (winreg) senão
   "C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe";
   ausente → fallback IE (E.7) se RUMBAPY_LAUNCH != "launcher", senão RumbaNotInstalled.
   Popen([exe, launcher_arg(p)], stdin/stdout/stderr=DEVNULL, close_fds=True,
         creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB) (fallback sem breakaway);
   grava o PID em run\launched-<tag>-<L>.json; t_lanc = monotonic(). Sandbox → SandboxViolation.
5. Registro, a cada 250 ms até min(D - 15, t_lanc + 45):
   - admin.sessions() contém L → sai (seconds_to_register).
   - proc.poll() not None e != 0 → LaunchFailed. Código 0 NÃO é falha (launcher de instância única, R7): segue no laço.
   - diálogo "Short Name already in use" em processo launcher/iexplore → click OK, limpeza (passo 10), MultipleInstances (mensagem de open).
   - window == "minimized" e já existe instância nova (instâncias − antes) → show(SW_SHOWMINNOACTIVE) cedo.
   - estourou → LaunchTimeout(fase "a letra não foi registrada") + limpeza.
6. owner = get_worker(L, "owner"); connect a cada 500 ms até D - 5:
   connected=true → segue | rc 1 → continua (o diálogo "Connecting" é normal) | TerminalInUse (rc 11) → mensagem com o
   servidor legado se E.8 o viu ocupado | HllapiSystemError 3 seguidos → levanta | estourou → LaunchTimeout(fase "conexão")
   + "o RUMBA ainda está tentando conectar — verifique VPN/rede" se o diálogo Connecting estiver visível; limpeza.
7. Primeira tela (até D): snapshot com busy=false, texto não totalmente em branco, oia.online e oia.ready, igual ao snapshot
   anterior tirado ≥ 0,3 s antes → ok; estourou → LaunchTimeout(fase "primeira tela"). Nada é digitado.
8. backend.bind_instance(hwnd) (na prática logo depois do connect, antes do passo 7: A.10 c); Session criada com OpenInfo.
9. Aplica window ("normal" → nada; "minimized" → SW_SHOWMINNOACTIVE) e devolve.
10. Limpeza (toda falha depois do passo 4): close_all restrito às instâncias novas (instâncias − antes) ∪ hwnd HLLAPI ∪
    PIDs lançados por nós. Nunca TerminateProcess.
```
`OpenInfo = {launched, closed_instances, hwnd, hwnd_pid, seconds_to_register, seconds_to_connect, rows, cols, via: "launcher"|"ie"|"reuse"}`.

### E.6 `attach(profile, timeout=10)` e janela

* `attach`: neutraliza o legado; `inst = instâncias`; 2+ → `MultipleInstances`; `admin.sessions()` sem a letra e 0
  instâncias → `TerminalNotOpen`; `get_worker(L, "owner")` (dono vivo → `TerminalInUse`); laço `connect` até o prazo;
  passo 7 de E.5 com o prazo restante; `bind_instance`. Nunca abre nem fecha janelas.
* `Session.show()` = `show(hwnd, SW_SHOWNOACTIVATE=4)`; `minimize()` = `SW_SHOWMINNOACTIVE=7`; `window` = `WindowInfo`
  (`hwnd, pid, title` sanitizado, `process, kind, visible, minimized`). Nenhuma ação ativa a janela (I16).

### E.7 Fallback Internet Explorer (só sem launcher ou `RUMBAPY_LAUNCH=ie`; nunca depois de falha do launcher)

`_ie_open.js` = **cópia do script validado ao vivo** (`GetObject("new:{D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E}")`,
InternetExplorerMedium; sem fallback para `InternetExplorer.Application`, C14/E31). Mudanças permitidas: 3º argumento
opcional `timeout_s` (padrão 60) e `out()` escapando não-ASCII como `\uXXXX`. Chamada:
`C:\Windows\System32\cscript.exe //NoLogo //E:JScript <pkgdir>\_ie_open.js <index_url> "<link_text>" <timeout_s>`,
`CREATE_NO_WINDOW`, prazo `timeout_s + 10`, saída decodificada com `errors="replace"`. `ok:false` → `LaunchFailed` com
`step`/`error` sanitizados. Depois segue E.5 a partir do passo 5 (sem `proc.poll`); a instância nova é a `ie` que apareceu
(instâncias − antes); o diálogo "Short Name" é procurado em processos `iexplore.exe`.

### E.8 Neutralização de servidores 0.1.5 (`_legacy.neutralize(letter)`)

Portas: D 8500, A 8600, Z 8700, outras 8800. Antes do 1º `connect` de `open`/`attach`, uma vez por processo e letra:
1. `pid = _win32.tcp_listener_pid(porta)`; `None` → nada. Sessão do Windows de `pid` ≠ a nossa → não tocar (WARNING, E33).
2. `socket.create_connection(("127.0.0.1", porta), timeout=0.5)`: recusado → nada; timeout → marca "presente e ocupado"
   (usado na mensagem de rc 11; R27).
3. Envia `{"session_id":"rumbapy2-migracao","action":"ping","params":{"terminal_type":L}}`, `shutdown(SHUT_WR)`, lê até
   EOF (2 s). Sem `success: true` → WARNING e para. `"shim": true` → nada (o stub sai sozinho).
4. 0.1.5 verdadeiro → `action: "disconnect"` com o mesmo `session_id` (libera o HLLAPI daquele processo). Repete 1–4 até 3
   vezes enquanto outro servidor não-shim responder (`SO_REUSEADDR`). WARNING: "Servidor legado v0.1.5 (porta 8500)
   desconectado do terminal D; ficará ocioso até o logoff." **Nunca** `close_terminal`; nunca encerrar o processo.

---

## F. Bindings ctypes, estruturas, codec e rc → exceção

### F.1 Carga (`_worker/hllapi.load_dll`)

```python
DLL_PATH = r"C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll"   # nunca copiar a DLL (.SHARDAT)
H_APP = ctypes.c_void_p(1)                                                    # 1º argumento de toda WD_*
def load_dll(path: str, *, fake: str | None, guard: Literal["none", "readonly", "text", "login"]) -> object
    # fake "mod:fábrica" -> importlib.import_module(mod).<fábrica>()  (só com RUMBAPY_SANDBOX=1; FakeEhllapi)
    # sandbox sem fake -> SandboxViolation (saída 17); real -> ctypes.WinDLL(path) DENTRO de T-dll
    # guard != "none" -> guard.GuardedDll(dll, POLICIES[guard])
POLICIES = {"readonly": GuardPolicy(set(), allow_text=False, allow_local_keys=False),
            "text":     GuardPolicy(set(), allow_text=True,  allow_local_keys=False),
            "login":    GuardPolicy({"ENTER"}, allow_text=True, allow_local_keys=False)}
```
`GuardViolation` (da `guard.py`, `PermissionError`) é traduzida pelo `Dispatcher` para o tipo de wire `GuardViolation`
(`delivery="none"`). Em produção não há guard (a lista fechada de bindings de F.2 já impede funções perigosas).

### F.2 Bindings e classe `Hllapi`

`H = c_void_p`, `W = c_ushort`, `PW = POINTER(c_ushort)`, `S = c_char_p` (buffers de `create_string_buffer`). **Só estas**
funções são vinculadas (teste estático): `WD_ConnectPS [H,S]`, `WD_DisconnectPS [H]`, `WD_ResetSystem [H]`,
`WD_SetSessionParamEx [H,S]`, `WD_SendKey [H,S]`, `WD_CopyPS [H,S,W]`, `WD_CopyPSToString [H,W,S,W]`,
`WD_CopyStringToPS [H,W,S,W]`, `WD_CopyFieldToString [H,W,S,W]`, `WD_QueryCursorLocation [H,PW]`, `WD_SetCursor [H,W]`,
`WD_QueryFieldAttribute [H,PW,W]`, `WD_FindFieldPosition [H,PW,W,S]`, `WD_FindFieldLength [H,PW,W,S]`,
`WD_CopyOIA [H,S,W]`, `WD_QuerySessions [H,PW,S]`, `WD_QuerySessionStatus [H,S]` (todas `restype=W`) e
`WD_GetSessionHWND [H] → c_void_p`. Acesso sempre por `getattr(dll, "WD_x")` (compatível com `FakeEhllapi` e `GuardedDll`).

```python
class Hllapi:          # só devolve rc/bytes; quem converte rc é rc.check (F.6)
    def __init__(self, dll: object, *, track: Callable[[str], ContextManager[None]]) -> None   # track = watchdog.inflight
    def connect(self, letter: str) -> int
    def disconnect(self) -> int
    def reset_system(self) -> int
    def set_param(self, text: bytes) -> int
    def query_sessions(self) -> tuple[int, list[SessionEntry]]
    def query_status(self, letter: str) -> tuple[int, SessionStatus | None]
    def copy_ps(self, size: int) -> tuple[int, bytes]
    def copy_ps_to_string(self, pos: int, n: int) -> tuple[int, bytes]
    def copy_string_to_ps(self, pos: int, data: bytes) -> int
    def copy_field(self, pos: int, n: int) -> tuple[int, bytes]
    def send_key(self, chunk: bytes) -> int
    def cursor(self) -> tuple[int, int]                       # (rc, pos)
    def set_cursor(self, pos: int) -> int
    def oia(self) -> tuple[int, bytes]                        # 103 bytes
    def field_attr(self, pos: int) -> tuple[int, int]
    def find_field_pos(self, pos: int, code: bytes) -> tuple[int, int]   # code = b"T " | b"N "
    def find_field_len(self, pos: int, code: bytes) -> tuple[int, int]
    def hwnd(self) -> int | None
```
Buffers: sempre `create_string_buffer(n + 16)` e leitura `.raw[:n]`, nunca `.value`. `CopyPS`: `rows*cols + 64`;
`CopyOIA`: 110 com len 103; `QuerySessionStatus`: 32 com `buf[0] = letra`, lê `.raw[:20]`; `QuerySessions`:
`26*16 + 16` com `count = c_ushort(26)`; códigos de campo como `create_string_buffer(b"T ")`.

### F.3 Estruturas (`_worker/structs.py`; formato 32-bit, `HllapiStandard=false`; validadas ao vivo)

`SessionEntry` (16 B): `letter = e[0:1]`, `long_name = e[4:12].decode("cp1252").strip(" \0")`, `type = e[12:13]`,
`ps_size = <H @14` (ao vivo: `44 20×11 48 20 80 07` → D, `H`, 1920). `SessionStatus` (20 B): `letter = b[0]`,
`long_name = b[4:12]`, `type = b[12]` (`D`), `eab = b[13] & 0x80`, `rows = <H @14`, `cols = <H @16`, `host_cp = <H @18`
(ao vivo 0; não confiável). OIA: ver B.5. Fixtures de teste com esses bytes exatos.

### F.4 Posições e texto (`_codec.py`, puro; usado por `keys`, `session` e worker — I42)

```python
def to_pos(row, col, rows, cols) -> int          # (row-1)*cols + col; InvalidPosition fora de 1..rows / 1..cols
def to_rowcol(pos, rows, cols) -> tuple[int, int]
def check_span(pos, length, rows, cols) -> None  # length >= 1 e pos+length-1 <= rows*cols
def decode(raw: bytes) -> str                    # 0x00-0x1F -> 0x20; .decode("cp1252", "replace"); len == len(raw)
def encode(text: str, *, host: Literal["cics", "ims"], errors: Literal["strict", "replace"] = "strict",
           secret: bool = False) -> bytes
```
`encode`: (1) `unicodedata.normalize("NFC")`; (2) controle (`< 0x20`, `0x7F`) → `EncodingError` ("use Key.TAB / escreva
por linha"); (3) conjunto seguro: ASCII imprimível + `U+00A0–U+00FF`; **CICS (CCSID 1143)**: sem `¤` (U+00A4), com `€`
(U+20AC); **IMS (CCSID 037)**: sem `€` (C29b). Fora do conjunto: `strict` → `EncodingError` (sem caractere/posição se
`secret`); `replace` → `“”`→`"`, `‘’`→`'`, `–—`→`-`, `…`→`...`, resto `?` (WARNING sem o texto); (4) `.encode("cp1252")`.
`rows/cols` vêm sempre de `QuerySessionStatus` (IMS A = 32×80).

### F.5 Teclas no envio

* Limite de projeto **128 bytes** por `WD_SendKey` (a DLL recusa ≥ 256 com rc 2); `@` literal = `@@`; `CopyStringToPS`
  não interpreta mnemônicos (`write` nunca escapa; `type` sempre escapa).
* `Session` nunca manda AID junto com texto: o corpo vai numa op `send` (chunks de `encode_body`), a AID vai sozinha numa
  op `send` de 1 chunk (G.3). O worker revalida (C.4).
* A compat `send_key` reproduz o descarte da DLL: tokens após a 1ª AID são descartados com WARNING (C3).

### F.6 rc → exceção (`_worker/rc.py`; teste percorre todo rc de RUMBA_EHLLAPI §9 nestas funções, I36)

Genéricos: `12 → SessionStopped`, `36 → HostConnectionLost`, `9 → HllapiSystemError`, desconhecido → `HllapiError("rc N")`.

| Função | Sucesso | Tolerado | Erro |
|---|---|---|---|
| `ConnectPS` | 0, 4, 5 (conectado) | 1 → `connected=false` | 2 `ParameterError`; 11 `TerminalInUse` |
| `DisconnectPS` | 0 | 1 | — |
| `ResetSystem` | qualquer (log) | — | — |
| `SetSessionParamEx` | 0 | 2 → WARNING | — |
| `SendKey` | 0 | — | 1 `NotConnected`; 2 `ParameterError`; 4 `HostBusy`; 5 `KeyboardLocked`; 20 `UndefinedKey` |
| `CopyPS`, `CopyOIA` | 0 | 4, 5 (`busy`) | 1 `NotConnected` |
| `CopyPSToString` | 0 | 4, 5 (`busy`) | 1; 2 `ParameterError`; 7 `InvalidPosition` |
| `CopyStringToPS` | 0 | — | 1; 2; 5 `KeyboardLocked` (`none`); 6 `Truncated` (`partial`); 7 |
| `CopyFieldToString` | 0 | — | 1; 2; 6 `Truncated`; 7; 24 `FieldNotFound` |
| `QueryCursorLocation` | 0 | — | 1 |
| `SetCursor` | 0 | — | 1; 4 `HostBusy`; 7 |
| `QueryFieldAttribute` | 0 | 24 → `None` | 1; 7 |
| `FindFieldPosition` / `FindFieldLength` | 0 | 24 → `None`; 28 → tamanho 0 | 1; 2; 7 |
| `QuerySessions` | 0 | — | 2; 9 |
| `QuerySessionStatus` | 0 | — | 1 `TerminalNotOpen`; 2 |
| `GetSessionHWND` | ≠ 0 | 0 → `None` | — |

---

## G. Esperas (`waits.py` + métodos da `Session`)

### G.1 Princípios

1. `WD_Wait == 0` não diz que a próxima tela chegou (por isso `WD_Wait` nem é vinculado).
2. Toda espera é polling no cliente de `snapshot` (texto + cursor + OIA numa op). Nenhuma espera envia nada ao host.
3. Toda espera tem prazo; estourou → `WaitTimeout` (`TimeoutError`) com `screen` e mensagem que mostra **o que havia**.

### G.2 Motor (puro)

```python
Cond = Callable[[Screen], "Cursor | str | bool | None"]   # verdadeiro/não-None = satisfeita
def wait_until(cond: Cond, *, snapshot: Callable[[], Screen], timeout: float, describe: Callable[[Screen], str],
               error_checks: Sequence[Callable[[Screen], None]] = (), clock=time.monotonic, sleep=time.sleep,
               fast_phase: float = 1.0, fast_poll: float = 0.05, slow_poll: float = 0.1,
               host_lost_after: float = 3.0, exc: type[WaitTimeout] = WaitTimeout) -> Screen
```
A cada snapshot: `error_checks`; `oia.online == False` contínuo por `host_lost_after` → `HostConnectionLost` (R29;
**[a validar]** a máscara de G1); `cond`; prazo → `exc(describe(s), screen=s)`. Devolve `dataclasses.replace(s, match=m)`.

### G.3 Antes de teclas e depois de AID (C1, I7 — sem `AID_GRACE`)

```
wait_ready(timeout)            = wait_until(s.oia.ready and not s.host_busy)            # sem error_checks (C20)
ready_stable(timeout)          = wait_ready; depois snapshots a cada 50 ms até 2 consecutivos prontos com o mesmo
                                 (text, cursor) — no mesmo prazo
type(text, secret)             : estado ok → wait_ready → send(encode_body([text]), secret)
press(k) com k local           : estado ok → wait_ready → send([k.value])
press(k) com k AID             : estado ok → pre = ready_stable(timeout)
                                 r = send([k.value])                                    # AID sozinha, 1 chunk
                                 seen_lock = r.oia_after.keyboard_locked
                                 cond(s): s.oia.keyboard_locked → seen_lock = True; None
                                          seen_lock or s.text != pre.text or s.cursor != pre.cursor → s
                                 wait_until(cond, error_checks=self.error_checks, exc=NoHostResponse)
send_keys(s, secret)           : para seg em split_at_aids(tokenize_extra(s)):
                                   seg.body → wait_ready → send(encode_body(seg.body, secret=secret), secret)
                                   seg.aid  → press(seg.aid)
```
`pre` é tirado **depois** da digitação (o eco local não conta como resposta). `NoHostResponse` → `delivery="unknown"`,
Session `uncertain`. Estouro de `wait_ready` antes de tecla → `WaitTimeout` "O teclado continua bloqueado após {s} s
(G8={hex}). Se for erro de digitação ('X -f'), use t.press(Key.RESET)." (R28) — nunca envia RESET sozinho.
**[a validar no L6]**: linha do tempo do OIA a cada 20 ms após cada ENTER do logon (confirma `oia_after` com bloqueio).

### G.4 `wait_text`

| Forma | Condição | `match` |
|---|---|---|
| `wait_text("VOLVO", 1, 2)` | `s.get(1, 2, 5) == "VOLVO"` | `Cursor(1, 2)` |
| `wait_text("VOLVO", 1)` | `"VOLVO" in s.line(1)` | posição achada |
| `wait_text("VOLVO")` | `s.find("VOLVO")` | posição achada |
| `wait_text(re.compile(r"SP\d{4}T"), 1)` | `pattern.search(s.line(1))` | início do match |
| `wait_text(pat, 1, 74)` | `pattern.match(s.text, pos-1)` | `Cursor(1, 74)` |
| `gone=True` | negação | posição informada ou `None` |
| `case=False` | `casefold()` | — |

Validação imediata: `col` sem `row` → `ParameterError`; fora da tela → `InvalidPosition`; texto vazio → `ParameterError`.
Mensagens (citam no máximo `len(esperado)` caracteres da posição ou a linha 1; campos ocultos já vêm em branco):
* `Esperei {s} s por '{x}' em ({r},{c}), mas ali está '{atual}'.` Dica: "confira a tela (print(e.screen)) e a coordenada; se o host for lento, aumente timeout."
* `Esperei {s} s por '{x}' em qualquer lugar da tela. Linha 1 atual: '{linha1}'.` · `gone`: `'{x}' continua em ({r},{c}) após {s} s.`
* Toda mensagem termina com o estado do OIA (`oia.describe()`).

### G.5 `wait_any`, `wait_change`, `wait_cursor`

* `wait_any(options)`: testa **na ordem do dicionário**; valor `str` = em qualquer lugar; `re.Pattern` = `search` na tela;
  `(texto, row, col)` = posição fixa; `Callable[[Screen], bool]` (vale a verdade do resultado). `match` = chave. Timeout
  lista cada opção e o que havia (formato em A.11 d).
* `wait_change(since=None)`: `s.text != base.text or s.cursor != base.cursor` (`base = since` ou o 1º snapshot).
* `wait_cursor(row, col)`: `s.cursor == (row, col)`.
* `error_checks` rodam em `wait_text/wait_any/wait_change/wait_cursor` e na espera pós-AID — **não** em `wait_ready`.
  Sucesso de qualquer uma destas tira a Session de `uncertain`.

### G.6 Constantes (`waits.py`, não expostas)

| Item | Valor |
|---|---|
| prazo padrão | `Session.timeout` (30 s; `RUMBAPY_TIMEOUT`) |
| polling | 50 ms no 1º segundo, depois 100 ms |
| `READY_STABLE_GAP` | 0,05 s (2 snapshots) |
| `FIRST_SCREEN_STABLE` | 0,3 s (E.5 passo 7) |
| `HOST_LOST_AFTER` | 3,0 s |
| detector CICS | `screen.get(5, 31, 19) == "E R R O   G R A V E"` → `HostError(screen=s)` |
| compat `screen_load` | `wait_text(text, y, x, timeout=120, gone=text_diff)` + `error_checks` do perfil |

Custo: cada snapshot = 3 chamadas locais ao emulador (+ enumeração de campos uma vez por tela, D.6); nada trafega até o
mainframe. ~30 chamadas/s durante esperas.

---

## H. Auto-update 2.0 (obrigatório; `_bootstrap.py` + `_update.py`)

Invariantes: (1) o usuário roda o commit de **`refs/heads/master`** (C7); (2) a checagem acontece antes de qualquer
código que fale com o terminal; (3) worker e cliente têm o mesmo `build_id`; (4) **a única exceção é `RUMBAPY_DEV=1`**,
explícita, registrada em log e avisada em stderr a cada import (C5, I20, E38); (5) sem Git obrigatório; (6) falha de rede
**bloqueia** (`UpdateCheckFailed`; não há válvula offline na v2.0 — D1 continua pendente com o dono, K.2); (7) **nunca
reexecuta o script** (I23, I6, R13); (8) um commit quebrado no `master` se corrige sozinho quando o `master` for corrigido (E1).

### H.1 Quando roda

| Momento | Chamada | Comportamento |
|---|---|---|
| `import rumbapy` (papéis `user`/`cli`) | `ensure_latest(context="import")` | checagem completa |
| `rumbapy.open/attach`, `RumbaClient()` | `ensure_latest(context="open")` | reconsulta se a última checagem bem-sucedida deste processo tiver > 1 h (interativo: `sys.ps1`, `ipykernel`/`IPython` em `sys.modules`) ou > 12 h (script) |
| `python -m rumbapy update` | `ensure_latest(context="cli", force=True)` | imprime o resultado, sem traceback |
| `client.update_python_64bit()` | `ensure_latest(context="legacy", force=True)` | instala e levanta `UpdateRequired` |
| papel `legacy-shim` | — | nunca |

### H.2 Modo de instalação (`_update.install_mode() -> tuple[mode, commit | None]`; E11)

`here = Path(rumbapy.__file__).resolve().parent`. Em ordem:
1. `os.environ.get("RUMBAPY_DEV") == "1"` → `("dev", None)`: log WARNING `update: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)
   versão=… script=…` + `notice("RumbaPy: AUTO-UPDATE DESLIGADO (RUMBAPY_DEV=1)")`.
2. `.git` em algum de `here.parents[:4]` → `UpdateFailed("Instalação de desenvolvimento detectada (checkout). Defina
   RUMBAPY_DEV=1 se você é desenvolvedor, ou instale com: <comando de recuperação>")` (C5: nada de dev implícito).
3. `dist` = a distribuição de `importlib.metadata.distributions(name="rumbapy")` com
   `dist.locate_file("rumbapy/__init__.py").resolve() == here / "__init__.py"`; nenhuma e sem `.git` → `UpdateFailed` (`missing`).
4. `direct_url.json` (`dist.read_text`): `dir_info.editable` → mesmo erro do passo 2; `url` casa `ARCHIVE_RE` com sha →
   `("archive", sha)`; `vcs_info.vcs == "git"` com `commit_id` → `("vcs", commit_id)` (instalações 0.1.x); senão
   `("unknown", None)` (reinstala o master uma vez, com `notice`).
```python
ARCHIVE_RE = re.compile(r"^https://(?:github\.com/[^/]+/[^/]+/archive/([0-9a-f]{40})\.zip"
                        r"|codeload\.github\.com/[^/]+/[^/]+/zip/([0-9a-f]{40}))$", re.I)
```
Comando de recuperação (README, `UpdateFailed`): `python -m pip install --force-reinstall --no-deps
https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip`.

### H.3 Commit remoto (`_bootstrap.remote_master_sha() -> str`)

1. `GET https://github.com/CamargoEduardo/RumbaPy.git/info/refs?service=git-upload-pack` (`urllib`; cabeçalhos
   `User-Agent: git/2.45 (rumbapy/<versão>)`, `Cache-Control: no-cache`, `Pragma: no-cache`; sem `Git-Protocol`; timeout
   5 s; 2 tentativas com 1 s entre elas; proxy do sistema; `ssl.create_default_context()`). `Content-Type` ≠
   `application/x-git-upload-pack-advertisement` → erro de rede (proxy/portal cativo; nunca "parsear" HTML).
   Parse de pkt-lines; achar a linha `<sha40> refs/heads/master` (validar `[0-9a-f]{40}`); ausente → erro de configuração.
   `symref=HEAD:` diferente de master → só WARNING.
2. Falhou e `git` no PATH → `git ls-remote https://github.com/CamargoEduardo/RumbaPy.git refs/heads/master` (10 s).
3. Falhou → `GET https://api.github.com/repos/CamargoEduardo/RumbaPy/commits/master` com `Accept: application/vnd.github.sha`
   (limite de 60/h por IP: último recurso).
4. Tudo falhou → `network` (DNS, timeout, TLS, 5xx, 429, Content-Type) ou `config` (404/401, sem master) →
   `UpdateCheckFailed` com o motivo.

### H.4 Algoritmo `ensure_latest(context, force=False)`

```
papel legacy-shim → return
_restart_required (variável de MÓDULO, nunca os.environ; E8, R26) → raise UpdateRequired (mesma mensagem)
já checado neste processo, sem force e sem TTL vencido (H.1) → return
mode, local = install_mode()                         # dev → return (log + notice)
sem force e cache válido → marca; return             # cache = update-state.json; válido só se result ∈ {"up-to-date","updated"}
                                                     #   e remote == local == commit instalado e idade < 60 s (E7)
remote = _bootstrap.remote_master_sha()              # falha → UpdateCheckFailed
grava cache (result = "up-to-date" se remote == local)
remote == local → marca; return
com _locks.file_lock("update", timeout=600):         # fatias de 0,1 s; estourou → UpdateFailed("outra atualização não terminou")
    interrompido = update-in-progress.json existe com PID morto (pip anterior abortado; E6)
    grava update-in-progress.json {pid, created, remote, at}
    atual = _bootstrap.fresh_installed()["commit"]   # interpretador novo (E10)
    se atual != remote ou interrompido: rc = _bootstrap.pip_install(remote); rc != 0 → UpdateFailed("pip código {rc}")
    fresh_installed()["commit"] != remote → UpdateFailed("após instalar, o commit ainda é …")
    apaga update-in-progress.json; cache result = "updated"
log INFO; _restart_required = remote; raise UpdateRequired(mensagem do contexto)
```
`UpdateRequired` por contexto: script → "Rode o script de novo."; Jupyter/IPython → "Reinicie o kernel (Kernel → Restart) e
rode a célula de novo."; REPL/IDLE → "Feche este Python e abra de novo."; `open`/`legacy` → "Este processo ainda usa a
versão antiga; reinicie-o."; `cli update` → imprime "RumbaPy atualizado para {v} ({sha7})." e sai com 0.

`_bootstrap.pip_install(sha) -> int` (E6): argv `[sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
"--no-input", "--force-reinstall", "--no-deps", f"https://github.com/CamargoEduardo/RumbaPy/archive/{sha}.zip"]`
(+ `--user` só se fora de venv, `site.ENABLE_USER_SITE` e o site-packages sem escrita); `Popen` desacoplado
(`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, `stdin=DEVNULL`, saída em `logs\pip-<ts>.log`);
**sem prazo e sem kill**: `notice("RumbaPy: atualizando para {sha7}…")` e, aos 300 s, "a atualização está demorando…".
`pip` ausente → dica `python -m ensurepip --upgrade`.

`_bootstrap.fresh_installed() -> dict`: `subprocess.run([sys.executable, "-E", "-c", SONDA], timeout=60)`, onde `SONDA`
usa só `importlib.util.find_spec("rumbapy").origin` (não importa o pacote) e a distribuição casada por `locate_file`,
e imprime `{"file", "url", "commit"}` em JSON.

### H.5 Sem reexecução na v2.0

Depois de instalar, **sempre** `UpdateRequired` (custa uma execução extra por update). A reexecução automática (com Job
Object `KILL_ON_JOB_CLOSE | BREAKAWAY_OK`, R13) fica adiada para a v2.1, condicionada ao aval do dono.

### H.6 `_bootstrap.py` congelado e `heal` (E1, E39)

* Só stdlib; nunca importa `rumbapy.*`. API: `say(msg)` (stderr se `sys.stderr` não for `None`, ignora `OSError`/
  `ValueError`, respeita `RUMBAPY_QUIET=1`; E25), `log_line(msg)`, `remote_master_sha()`, `pip_install(sha)`,
  `fresh_installed()`, `heal(exc) -> NoReturn`. `_update` usa essas primitivas (não as reimplementa).
* Congelamento: `_build/bootstrap.sha256` guarda o SHA-256 do arquivo; teste de CI falha se divergir. Mudar o bootstrap
  = atualizar o hash no mesmo commit, com teste manual do `heal` registrado no PR.
* `heal(exc)`: registra o traceback em `update.log`; com `RUMBAPY_DEV=1` relança `exc`; se `update-in-progress.json`
  tiver PID vivo, espera até 60 s e levanta "O RumbaPy está sendo atualizado por outro processo; rode de novo em alguns
  segundos."; senão `remote = remote_master_sha()` (falhou → `RuntimeError` com o comando de recuperação); se
  `heal-state.json` já registrar ≥ 1 tentativa para esse `remote` há < 1 h → `RuntimeError("A versão publicada do RumbaPy
  ({sha7}) está com defeito; avise o responsável. Detalhes em update.log")`; senão `pip_install(remote)` sob o lock de
  update, grava `heal-state.json` e levanta `RuntimeError("O RumbaPy foi reparado para {sha7}. Rode de novo.")`.

### H.7 Empacotamento sem PyPI (E12, E29)

```toml
[build-system]
requires = []
build-backend = "rumbapy_build"
backend-path = ["_build"]
```
`_build/rumbapy_build.py` (só stdlib): `get_requires_for_build_{wheel,sdist,editable}` → `[]`; `prepare_metadata_for_build_wheel`;
`build_wheel` (`py3-none-any`; todo arquivo de `src/rumbapy/` menos `__pycache__`; `dist-info` com `METADATA` (2.1),
`WHEEL`, `RECORD` com SHA-256 urlsafe-b64, `top_level.txt`); `build_sdist`; `build_editable` (`.pth` para `src`; só dev).
Versão lida de `src/rumbapy/_version.py` por regex; demais metadados de `[project]` via `tomllib`. `.gitignore`: padrões de
dados ancorados na raiz (`/*.xlsx`, `/data/`, …) e exceções `!src/**`, `!tests/**`.

### H.8 Migração dos usuários na v0.1.5

1. Script antigo: `RumbaClient.__init__` **0.1.5 em memória** instala a v2 no 32-bit e no 64-bit (`pip … git+https`, que
   usa o backend in-tree). 2. Se houver servidor 0.1.5 vivo, ele continua atendendo (código velho); senão o 0.1.5 sobe
   `python32 -m rumbapy.server` = **stub v2** (B.9.4), que responde "atualizado: rode de novo". 3. Próxima execução: v2
   (`mode=vcs`, commit = master). A partir daí, updates por `archive/<sha>.zip`; servidores 0.1.5 são neutralizados (E.8).
Obrigações: instalável por `git+https`; `import rumbapy` em 32-bit não quebra; `rumbapy.server` existe; sem dependências;
`rumbapy.client.RumbaClient` importável. CHANGELOG: "reinicie todos os kernels/scripts antigos após a v2" (E32).

### H.9 Observabilidade e CLI

`update.log`: `2026-10-03 14:00:01.120 INFO  update context=import mode=archive local=1a2b3c4 remote=9f8e7d6 result=updated pip_s=7.4`.
`python -m rumbapy version` → versão, commit, modo, última checagem; `update` → força; `doctor` inclui alcance do
`info/refs`, modo de instalação, workers vivos (registros + `ping`), servidores 0.1.5 nas portas, caminho dos logs.

### H.10 `build_id` (`_buildid.compute(pkgdir) -> str`, puro, mesmo código nos dois lados)

```python
files = sorted(p for p in Path(pkgdir).rglob("*")
               if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".js", ".json"})
h = hashlib.sha256()
for p in files:
    h.update(p.relative_to(pkgdir).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
return h.hexdigest()[:16]
```
Calculado no `import` (A.6, duas vezes: antes e depois dos imports), antes de cada spawn (D.8), pelo worker no boot
(D.2 passo 3) e pelo worker ao fim de cada lease (C.6.4).

---

## I. Python 32-bit (`_runtime32.py`, reduzido; I26)

### I.1 Runtime válido

Sonda `[exe, "-I", "-S", "-c", PROBE]` (timeout 10 s) que importa `ctypes, _ctypes, _overlapped, _winapi, _hashlib, hmac,
json, queue, threading` e imprime JSON `{"bits", "version": [x, y, z], "level", "real"}` com
`real = os.path.realpath(getattr(sys, "_base_executable", sys.executable))`. Válido se `bits == 32`, `version >= (3, 12)`,
`level == "final"` e código 0. O `Popen` do worker usa sempre `real` (venv/redirector, E14).

### I.2 Ordem de busca (`find(explicit=None) -> Runtime32(path, real, version)`; para no primeiro válido)

| # | Fonte | Observação |
|---|---|---|
| 1 | argumento (`python32=`, `python32bit_path=`) | inválido → `Python32NotFound` com o motivo (sem cair para os próximos) |
| 2 | `RUMBAPY_PYTHON32` | idem |
| 3 | `sys.executable`, se o processo atual for 32-bit | — |
| 4 | `runtime.json` `{"path","real","size","mtime","version"}` | revalida por tamanho+mtime |
| 5 | `py -0p` | formatos do launcher clássico (` -V:3.13-32  C:\…`) e do Python Install Manager (E15); testes com saídas gravadas |
| 6 | globs `%LOCALAPPDATA%\Programs\Python\Python3*-32\python.exe`, `%LOCALAPPDATA%\Python\pythoncore-3.*-32\python.exe`, `C:\Program Files (x86)\Python3*\python.exe` | dentro de cada glob, maior versão primeiro |

Uma descoberta por processo (cache em memória); grava `runtime.json`. Nada encontrado → `Python32NotFound`:
```
Não encontrei um Python 32-bit >= 3.12 (o RUMBA só existe em 32-bit).
  Como resolver: instale o "Windows installer (32-bit)" do Python 3.13 em python.org e rode de novo
  (ele será encontrado sozinho), ou informe o caminho: rumbapy.open("D", python32=r"C:\...\python.exe").
```

### I.3 Adiado (v2.1, após D3/TI)

Provisionamento automático do *embeddable* (validado ao vivo: 3.13.15, 9.758.828 bytes, SHA-256 `3f550636…f5ce`; o BOOT de
D.1 dispensa mexer no `._pth`), atrás de `RUMBAPY_PROVISION=1`. Deixam de existir: `pip install` no 32-bit, pywin32/
pywinauto, exigência de 3.13 no 32-bit (mínimo 3.12).

---

## J. Testes

Regra: **nenhum teste automatizado carrega a `ehlapi32.Dll`, abre o RUMBA, manda mensagem a janelas reais ou acessa o
mainframe** — garantido por código (J.2), não só por disciplina. Testes ao vivo são manuais e passam pelo `GuardedDll` (J.6).

### J.1 Ferramentas, matriz e portão do `master`

* `pytest` + `pytest-timeout` (30 s/teste), `ruff`, `pyright` (`pythonVersion="3.12"`, `pythonPlatform="Windows"`). Só de
  desenvolvimento (o pacote continua com `dependencies = []`).
* `.github/workflows/ci.yml` (`windows-latest`): `test` = matriz Python **3.12/3.13/3.14 × x64/x86** (`-m "not live"`;
  os e2e com worker real rodam nos jobs x86); `install-smoke` (x64 3.12/3.13/3.14): `git archive --format=zip HEAD` →
  servidor HTTP local que imita o GitHub (`info/refs` com o sha do HEAD e `archive/<sha>.zip`) → `venv` limpo →
  `pip install --no-deps <url local>` → `python -c "import rumbapy"` com `RUMBAPY_SANDBOX=1` e
  `RUMBAPY_TEST_GITHUB=<url local>` → confere `direct_url.json` (modo `archive`) → BOOT do worker em x86 com `--fake-dll`;
  `test_wheel_contents` (lista exata, inclui `_ie_open.js`, exclui `tests/ docs/ tools/`); `test_bootstrap_frozen` (H.6).
* `RUMBAPY_TEST_GITHUB` e `RUMBAPY_TEST_PIP` só são lidos com `RUMBAPY_SANDBOX=1` (logados); sem sandbox são ignorados.
* **Portão do `master`** (push = deploy): CI verde em `dev` + roteiro ao vivo L1–L6 executado pelo dono em `dev` + versão
  incrementada + `CHANGELOG.md`. Recomendado: proteção de branch (merge só por PR de `dev` com CI verde) — decisão do dono (K.2).

### J.2 Sandbox (`tests/conftest.py`, E3)

No topo do `conftest.py` raiz, **antes de qualquer import de `rumbapy`**: `RUMBAPY_SANDBOX=1`, `RUMBAPY_DEV=1`,
`RUMBAPY_QUIET=1`, `RUMBAPY_HOME=<pasta temporária da sessão>` — salvo quando `RUMBAPY_LIVE=1` (J.6). Com sandbox, o
**código de produção** recusa (`SandboxViolation`): DLL real (`load_dll`, saída 17), `Popen` do launcher e do `cscript`
(`_launcher`), `RealUser32.post_close/click/show` e `_win32.show_window_async`, `pip_install` sem `RUMBAPY_TEST_PIP`,
rede sem `RUMBAPY_TEST_GITHUB`. Testes-sentinela: cada recusa acima é exercitada e um monkeypatch derruba o teste se
`user32.PostMessageW` real for chamado.

### J.3 Dublês (reutilizar; não recriar)

* **`tests/fakes/fake_ehllapi.py` (existente)** — usado por todos os níveis. Extensões **aditivas e desligadas por padrão**
  (P-0): `FakeSession.response_delay` (a AID trava o teclado e o `on_aid` só roda depois do atraso, via `threading.Timer`;
  a tela antiga continua até lá); `FakeSession.lock_visible_after` (o bit de G8 só aparece N s depois da AID: simula
  bloqueio não observado); `FakeEhllapi.rc_override: dict[str, list[int]]` (fila de rc forçados por função);
  `WD_SetSessionParamEx` passa a remover a opção oposta (`NORESET` tira `AUTORESET`, `ATTRB`/`NOATTRB`, `SRCHALL`/`SRCHFROM`…).
* `tests/fakes/guard.py` → reexporta `rumbapy._worker.guard` (o arquivo foi movido com `git mv`, lógica inalterada).
* `tests/fakes/fake_user32.py` (novo, P-0): janelas, processos (imagem, pai), diálogos com dono/textos/botões; `post_close`
  fecha após atraso configurável salvo se houver diálogo bloqueando; registra todas as chamadas; não tem método de matar processo.
* `tests/fakes/scenarios.py` (P-0): fábricas para `--fake-dll` — `cics_login()`, `blank_first_screen(seconds)`,
  `slow_host(response_delay)`, `hang(func)`, `two_letters()`; cada uma devolve um `FakeEhllapi` configurado
  (via `make_cics_login_host` quando couber).
* `tests/fakes/fake_backend.py` (P-B): `InProcessBackend(fake: FakeEhllapi, letter)` = `Dispatcher(Engine(Hllapi(fake)))`
  no próprio processo, com `json.dumps/loads` em cada mensagem.
* Pontos de injeção: `session._set_factories(backend_factory, user32_factory, launcher_factory)` (A.11 a);
  `_launcher.Launcher(user32, get_backend, spawn, clock=time.monotonic, sleep=time.sleep)` (`spawn(argv) -> ProcessLike`
  com `.pid`, `.poll()`); `Watchdog(exit_fn=os._exit, clock=…)`; `waits.wait_until(clock=…, sleep=…)`.

### J.4 Camadas

| Camada | Pasta | Dublês | Prova |
|---|---|---|---|
| Pura | `tests/unit/core` | nenhum | codec, keys, screen, waits (relógio falso), structs, mask, classify/sanitize, rc→exceção, install_mode/ARCHIVE_RE/pkt-line |
| Worker em processo | `tests/unit/worker` | `FakeEhllapi`, `GuardedDll`, `exit_fn` | dispatch, validações, engine, delivery, máscara, watchdog (sem matar o pytest) |
| Cliente | `tests/unit/client` | `InProcessBackend`, `FakeUser32`, `spawn` falso | session, receitas, compat, launcher, close_all, legado |
| Ponta a ponta | `tests/e2e` | worker **real** em subprocess com `--fake-dll tests.fakes.scenarios:<f> --fake-path <repo>` | pipe+DACL, auth, handshake, lease, evict, reciclagem, spawn, códigos de saída, logs |
| Update | `tests/unit/update`, `tests/e2e/update` | GitHub local, pip falso (`RUMBAPY_TEST_PIP` grava argv), `RUMBAPY_HOME` temporário | H inteiro, heal |
| Ao vivo | `tests/live` | nenhum (RUMBA real + guard) | J.6 |

### J.5 Testes de segurança transversais (obrigatórios; dono: P-G, com partes em cada pacote)

* **T-SEC-1 senha nunca aparece**: logon com campo oculto no `FakeEhllapi`; procurar a senha em `str(screen)`,
  `screen.text`, `screen._repr_html_()`, `str(exc)`/`exc.screen` de todas as exceções provocadas, `repr(session)`,
  `worker-*.log` e `client-*.log` em DEBUG, `boot\*.txt`, saída de `doctor()` e em **todo JSON trafegado** (espião no pipe).
  Inclui `send_keys("A000000<Tab>senha<Enter>")` **sem** `secret=True`.
* **T-SEC-2 sem dados internos**: nenhum arquivo versionado contém os padrões de hostnames internos (lista em segredo
  do CI, não no repositório).
* **T-SAFE-1 sandbox**: sentinelas de J.2.
* **T-SAFE-2 nenhuma AID a mais**: para cada API de entrada (`press`, `send_keys`, receitas, compat `send_key`, `screen_load`),
  o `aid_log` do `FakeEhllapi` tem exatamente as AIDs esperadas (`send_key("A@EB@E")` → 1 AID; `PAGE_DOWN` conta como AID).
* **T-SAFE-3 nada antes da tela**: `send_keys("a<Enter>b<Enter>")` com `response_delay=0.5` e `lock_visible_after=0.3`
  não envia a 2ª AID antes de a tela mudar; `open` com `blank_first_screen(2)` só devolve depois da 1ª tela.

### J.6 Testes ao vivo (manuais; terminal de PRODUÇÃO)

Pré-condições: branch `dev`; `RUMBAPY_LIVE=1`; `RUMBAPY_GUARD` obrigatório (`readonly|text|login`, repassado ao worker
como `--guard`; sem ele o worker sai com 16); confirmação digitada `SIM <letra>` lida de `CONIN$` (sem console → teste
**pulado**, E30); `pytest tests/live -m live -p no:randomly -x`; credenciais só por `getpass`; logs em pasta temporária
varridos (T-SEC-1) ao final; `policy.audit` do guard anexado ao relatório.

| Id | O que faz | Guard | Entrada no host | Autorização |
|---|---|---|---|---|
| L1 | `open("D", window="minimized")`: tempos de registro/conexão, rc do `SetSessionParamEx("NORESET")` (parâmetro local da conexão HLLAPI; listado por transparência), 1ª tela, `snapshot(fields=True)` (enumeração), cursor (22,56); `close` → 0 instâncias | readonly | não | permitido |
| L2 | 2ª instância aberta à mão → `open("D")` fecha as duas; registrar o diálogo "Short Name" e o efeito do `click OK`; PIDs | readonly | não | permitido |
| L3 | `attach` → `release` → `attach` de outro processo; `rumbapy.close` com dono vivo (evict); reciclagem por `shutdown`; `read` de novo | readonly | não | permitido |
| L3b | worker conectado sai por `os._exit(3)` sem `DisconnectPS` (simula watchdog); medir rc/tempo do `ConnectPS` do worker novo (R8) | readonly | não | permitido (só conexão local) |
| L4 | `read_field` de um campo desprotegido; `fill` com `TESTE çãÉ€` (≤ tamanho); ler de volta; `fill("")`; **terminar com `close()`** (MDT fica ligado, C25); nunca ENTER depois | text | só PS local | permitido ("escrever e apagar") |
| L5 | `show`/`minimize` com leituras entre eles; título da janela em cada estado | readonly | não | permitido |
| L6 | `t.cics.logon` com o login do dono; linha do tempo do OIA a cada 20 ms após cada ENTER (inclui `oia_after`); ler as âncoras de MENU/SIGNON/OK/MSG e gravá-las em `docs/interno/`; `close()` com sessão logada (diálogo de confirmação?) | login | **login** (ENTER) | permitido (login) |
| L7 | `t.ims.logon` (RHELP) | login | login em outro sistema | **pedir autorização explícita antes** |
| L8 | servidor 0.1.5 real em 32-bit: neutralização (`ping` + `disconnect`) | readonly | não | permitido |
| L9 | com BRAZIL_Z aberto, `open("D")`: processos/PIDs do launcher (instância única? R7) | readonly | não | permitido |
| — | cancelar o "Connecting" à mão (títulos com sessão desconectada, R6e); provocar `X -f` para mapear G8 (R28); qualquer PF/PA/transação ou escrita em tela de negócio | — | interação/teclas | **proibido sem plano escrito (objetivo, telas, teclas exatas, como desfazer) aprovado pelo dono** |

---

## K. Riscos e decisões

### K.1 Riscos remanescentes

| # | Risco | Mitigação nesta especificação | Resta |
|---|---|---|---|
| K-R1 | Push no `master` = deploy para todos | install-smoke, `heal` (H.6), portão L1–L6, `_bootstrap` congelado | proteção de branch (Q6) |
| K-R2 | GitHub/proxy fora do ar bloqueia todos (fail-closed) | 3 fontes de HEAD; mensagem com como fechar terminais pelo X | D1 (Q1) |
| K-R3 | Bits do OIA (máscara de G1, bits de G8) não validados | só "G8 ≠ 0 = bloqueado" decide; `HOST_LOST_AFTER` usa G1 & 0x30 | L1/L6 |
| K-R4 | Modelo de processo do Web Launcher com 2 perfis | `poll()==0` não é falha; instância nova por diferença de conjunto + HWND | L9 |
| K-R5 | Confirmação ao fechar sessão logada | diálogo desconhecido no close → `post_close` do diálogo; `CloseFailed` claro | L6 |
| K-R6 | rc 11 após `os._exit` do watchdog | reconexão tolera rc 11 por 15 s; mensagem orienta `open` | L3b |
| K-R7 | Breakaway negado ⇒ worker morto pelo Job da IDE ⇒ 1 slot vaza | WARNING; `ClientTableFull` com instrução | logoff |
| K-R8 | `evict` interrompe o lote de outro script quando alguém faz `open`/`close` da mesma letra | é o pedido do dono; o outro script recebe `SessionReplaced` (nunca digita na tela nova) | Q5 |
| K-R9 | `NORESET` muda o comportamento da 0.1.5 (AUTORESET) | falha explícita `KeyboardLocked` + dica `Key.RESET` | Q4 |
| K-R10 | `UpdateRequired` uma vez por deploy (tarefas agendadas falham 1 execução) | mensagem clara; reexecução adiada | Q2 |
| K-R11 | Escrita espontânea do host em campo oculto sem AID nossa | detecção por hash do PS no `snapshot` (D.6) | — |
| K-R12 | Abas do Edge no modo IE seguram a letra | `InstanceNotClosable` com dica | usuário fecha |
| K-R13 | `.SHARDAT` global em máquina multiusuário | colisão de PID detectada; neutralização só na própria sessão | fora do escopo |
| K-R14 | Repositório público com engenharia reversa | dados internos só em `docs/interno/` | D7 (Q8) |
| K-R15 | Conjunto seguro de caracteres (`€` no IMS, `¤` no CICS) | `EncodingError` com dica | L4 só em D |

### K.2 Questões para o dono (padrão adotado entre colchetes)

| # | Questão | Padrão |
|---|---|---|
| Q1 | D1: válvula offline (`RUMBAPY_ALLOW_OFFLINE`) | **[não existe na v2.0]**; se aprovada: só com checagem bem-sucedida < 24 h e commit local = remoto daquela checagem |
| Q2 | Reexecução automática após update (v2.1, com Job Object) | **[não]** |
| Q3 | Mudanças de semântica da compat (B.9.1: `send_key`/`copy_string_to_ps`/`connect` levantam; `logon_rhelp` só em perfil IMS) | **[aceitar]** — todas evitam perda silenciosa |
| Q4 | `NORESET` após o `ConnectPS` | **[sim]** |
| Q5 | `open`/`close` revogam o lease de outro script da mesma letra | **[sim]** (pedido do dono) |
| Q6 | Proteção do `master` + CI obrigatório | **[recomendado]** |
| Q7 | D3: download automático do Python 32-bit | **[adiado para v2.1]** |
| Q8 | D7: publicação da engenharia reversa | pendente (jurídico/Segurança) |
| Q9 | Testes ao vivo além do permitido (L7, cancelar "Connecting", mapear G8) | **[só com plano aprovado]** |
| Q10 | Canal `dev` para o dono testar em outras máquinas | **[não; use `RUMBAPY_DEV=1` + checkout]** |
| Q11 | `with` ao sair: fechar o terminal | **[não]** (`close_on_exit=True` opcional) |

### K.3 Validações ao vivo antes do `master`

L1 (NORESET, 1ª tela, enumeração de campos), L2 (diálogo Short Name/`click OK`), L3/L3b (lease, evict, rc pós-watchdog),
L4 (acentos e `€`), L6 (linha do tempo do OIA, âncoras do logon, fechamento logado), L8 (0.1.5), L9 (launcher com 2 perfis).

### K.4 Escopo cortado/adiado (v2.1 ou nunca)

Adaptador `rumbapy.extra`; apelidos snake_case (`get_string`, `put_string`…); `rumbapy.testing` público; `Screen.diff/
region/find_all/to_dataframe`; `wait_settle`, `clear_field`, `tab`, `read_line`, `contains`, propriedades `cursor`/`oia`
da Session; `hide`, `front`, `window="hidden"`; `register_profile`; `rumbapy.sessions()` e CLI `sessions/stop/logs`; shim
completo do protocolo 1 e `LocalBackend`; ops `search/cursor/oia/field_attr/write_field/wait_kb/cancel/diag`; reexecução
após update; provisionamento do embeddable e busca no registro PEP 514; válvula offline; canal `dev`; bits finos do OIA
(`system_wait`/`operator_error`/`insert`/`comm_error`); preempção de lease por inatividade (substituída por `evict` em
`open`/`close`). **Nunca**: backend TN3270 direto, chaves `kc1..kc4`, hospedar o ActiveX.

---

## L. Pacotes de implementação

Regras para todos: trabalhar em `dev` (um PR por pacote para `dev`); nada vai ao `master` sem pedido do dono; nenhum
teste carrega a DLL real (J.2); identificadores em inglês, mensagens/logs/comentários em português; imports na
convenção do CLAUDE.md. **Pronto** = testes de aceite do pacote verdes em x64 e x86 (3.12–3.14), `ruff` e `pyright`
limpos, `test_layering.py` e `test_contracts.py` verdes. Mudar um contrato = editar esta especificação no mesmo PR.

### Ondas

| Onda | Pacotes (paralelos dentro da onda) | Depende de |
|---|---|---|
| 0 | **P-0 Contratos** (1 pessoa; o merge libera as demais) | — |
| 1 | **P-A** núcleo puro · **P-B** worker + IPC · **P-F** update/empacotamento/CI | P-0 |
| 2 | **P-C** janelas/launcher · **P-D** ponte/runtime/doctor · **P-E** API/receitas/compat | P-0, P-A, P-B (marco B1 basta para P-C/P-E) |
| 3 | **P-G** integração, segurança, testes ao vivo, documentação | todos |

### P-0 — Contratos (Onda 0)

* **Arquivos**: `_version.py`, `_paths.py`, `_locks.py`, `_log.py`, `_buildid.py`, `_protocol.py` (`Op`, `OPS`, limites,
  `validate_request`, `encode`/`decode` de mensagens), `_types.py`, `errors.py` (completo), `keys.py` (`Key`, `AIDS`, `Token`,
  `Segment`, `tokenize_mnemonic` completo — usado pelo worker e pela compat — + assinaturas de P-A com `NotImplementedError`), `screen.py` (dataclasses, `OIA.from_raw`, `Screen.from_wire`;
  métodos de P-A como stubs), `profiles.py`, `_backend.py` (só `Backend`), `_user32.py` (só `User32`/`RawWindow`),
  `_win32.py` (assinaturas de D.9 com stubs), `_codec.py` (completo, F.4 — pequeno e usado por P-A e P-B), `_worker/__init__.py`, `_worker/hllapi.py` (assinaturas de `Hllapi`/`load_dll`,
  `POLICIES`), `git mv tests/fakes/guard.py src/rumbapy/_worker/guard.py` + reexport, `git rm -r --cached src/rumbapy.egg-info`,
  `tests/conftest.py`, extensões de `tests/fakes/fake_ehllapi.py` (J.3), `tests/fakes/fake_user32.py`, `tests/fakes/scenarios.py`,
  `tests/unit/test_layering.py`, `tests/unit/test_contracts.py`.
* **Expõe**: todos os contratos de B.5–B.7, C.3–C.5, D.9, E.2, F.2, H.10, A.4–A.5.
* **Aceite**: `test_layering` (regras A.4); `test_contracts` (chaves de `OPS` == métodos de `Backend` menos
  `bind_instance/release`; tipos de args); `keys.AIDS` == AIDs de `fake_ehllapi.AID_KEYS/AID_A_KEYS` == `guard.AID_BY_CODE/
  AID_BY_A_CODE`; ida e volta `errors.to_wire/from_wire` para cada classe (tipo, `delivery`, herança `RuntimeError`/`ValueError`/
  `TimeoutError`/`IndexError`, `str()` no formato B.7, `pickle`); `OIA.from_raw` com os bytes ao vivo (G1=0x10, G8 zerado →
  `ready`); `_buildid.compute` estável e sensível a qualquer `.py/.js/.json`; `atomic_write` sob `PermissionError` simulado;
  `file_lock` exclusivo entre 2 processos e liberado na morte do dono; extensões do fake desligadas ⇒ comportamento antigo
  idêntico (`response_delay`, `lock_visible_after`, `rc_override`, `NORESET` remove `AUTORESET`); conftest liga o sandbox;
  `_codec`: `decode` troca `0x00–0x1F` por espaço e preserva o tamanho; `ç ã É €` ida e volta; bytes indefinidos → `U+FFFD`;
  `encode` NFC, controles → erro, `€` aceito em CICS e recusado em IMS, `¤` recusado em CICS, mapa de `replace`, mensagens
  `secret` sem caractere; `to_pos/to_rowcol/check_span` em 24×80 e 32×80.

### P-A — Núcleo puro (Onda 1)

* **Arquivos**: `keys.py` (`tokenize_extra`, `split_at_aids`, `encode_body`, `Key.parse/pf`; testes também de `tokenize_mnemonic`);
  `screen.py` (métodos de B.5); `waits.py` (G.2, constantes G.6). Testes em `tests/unit/core/`.
* **Expõe**: B.6, B.5, G.2. **Consome**: P-0 (inclusive `_codec`).
* **Aceite**: `tokenize_mnemonic("SPRE@E") == ["SPRE", Key.ENTER]`,
  `"@@"`→`"@"`, `"@A@F"`→`ERASE_INPUT`, `"@"` final → `UndefinedKey`; `tokenize_extra("abc<Tab>1<Enter>")`, `"<<"`;
  `split_at_aids` com `PAGE_DOWN` cortando; `encode_body` de 300 caracteres → 3 chunks ≤ 128 bytes, `@@` nunca partido, nenhuma
  AID no corpo; `Key.parse` aceita `F3/PF3/<Pf3>/PAGEUP/@3` e sugere para `PF33`; `Screen.table/find/contains/to_text`,
  `_repr_html_` escapa HTML; `wait_until` com relógio falso: sucesso, timeout com mensagem, `error_checks` antes da condição,
  `HostConnectionLost` após 3 s offline, `KeyboardInterrupt` no `sleep` interrompe na hora.

### P-B — Worker e IPC (Onda 1; marco **B1** = `hllapi`, `rc`, `structs`, `mask`, `engine`, `dispatch`, `fake_backend`)

* **Arquivos**: `_ipc.py`, `_win32.py` (implementação), `_worker/{main,server,dispatch,engine,hllapi,rc,structs,mask,watchdog}.py`,
  `tests/fakes/fake_backend.py`. Testes em `tests/unit/worker/` e `tests/unit/ipc/`.
* **Expõe**: C.1–C.4, C.6 (lado worker), D.1–D.7, D.9, F.1–F.3, F.6, `InProcessBackend`. **Consome**: P-0 (inclusive `_codec` e `keys.tokenize_mnemonic`).
* **Aceite**: `connect` com `connect_delay=11` (rc 1 → 0), `NORESET` aplicado, `already=true` na 2ª; `write` protegido →
  `ProtectedField` sem `CopyStringToPS` no `calls`; texto maior que o campo → `Truncated(delivery="none")` sem cópia; rc 6
  forçado → `partial`; `send` com rc 5 no 1º chunk de 1 mnemônico → `none`, no 2º → `partial`; `oia_after` presente; `send` com
  AID fora do último chunk → `ParameterError`; **máscara**: campo `hidden=True` preenchido nunca aparece em `snapshot/read/
  read_field` (antes e depois do ENTER), spans `secret` caem após a resposta, escrita espontânea do host (buffer alterado sem
  op) reenumera; `hidden_unknown` com `rc_override`; tabela F.6 inteira; `Dispatcher.HANDLERS` == `OPS`; `hwnd` divergente →
  `SessionReplaced`; watchdog com `hang_on={"WD_CopyPS"}` e `exit_fn` injetado: temporizador de 2 s armado **antes** da
  resposta, tipo `WorkerHung`, `delivery` correto; salto de relógio > 5 s não dispara; `is_window` falso 2 ticks → `SessionStopped`;
  `collides()`; IPC real em processo: chave certa ok, chave errada recusada nos dois sentidos, cliente mudo não bloqueia um 2º
  cliente (R2), mensagem > 1 MiB → `ProtocolError`, DACL criada (`GetSecurityInfo` contém só o SID do usuário e SYSTEM);
  servidor: `ping` respondido com T-dll ocupada, 2º `owner` → `reject busy`, `evict` despeja o dono (evento + `SessionReplaced`),
  `shutdown` com dono → `TerminalInUse`, evento de parada adiado com dono, EOF cancela jobs pendentes, fim de lease com build
  novo → saída 0; `main` em subprocesso (`--allow-64 --fake-dll`) sai com 12/13/14/16/17 nos cenários respectivos e 11 no 2º worker.

### P-F — Update, empacotamento e CI (Onda 1)

* **Arquivos**: `_bootstrap.py`, `_update.py`, `_role.py`, `__init__.py`, `__main__.py`, `_build/rumbapy_build.py`,
  `_build/bootstrap.sha256`, `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`. Testes em `tests/unit/update/`,
  `tests/e2e/update/`.
* **Expõe**: H inteiro, A.6, A.7, CLI. **Consome**: P-0 (`errors`, `_paths`, `_locks`, `_log`, `_version`, `_buildid`).
* **Aceite**: parse de pkt-line com fixture real de `info/refs` (gravada em 2026-10-03) e com fixture em que `HEAD → dev`
  (escolhe `refs/heads/master`); `Content-Type` HTML → erro de rede; matriz `install_mode` (DEV, checkout → `UpdateFailed`,
  editable → `UpdateFailed`, archive, codeload, vcs 0.1.x, unknown, missing, distribuição de outro caminho descartada);
  cache: `failed`/remote≠local nunca dispensam consulta; igual → nenhum pip; diferente → argv exato de H.4 (pip falso);
  `update-in-progress.json` com PID morto → reinstala; verificação em interpretador novo; `UpdateRequired` por contexto;
  `_restart_required` não vaza para subprocesso filho; `update_python_64bit` nunca reexecuta; `heal`: `SyntaxError` injetado em
  `_update.py` de uma cópia do pacote → reinstala via pip falso; 2ª falha no mesmo sha < 1 h → mensagem de defeito; papéis
  `-m rumbapy`/`-m rumbapy.server`/script; `sys.stderr = None` não quebra; `RUMBAPY_DEV=1` → `notice` a cada import;
  `test_wheel_contents`; `test_bootstrap_frozen`; job `install-smoke` verde.

### P-C — Janelas, launcher e legado (Onda 2)

* **Arquivos**: `_user32.py` (`RealUser32`), `_instances.py`, `_launcher.py`, `_legacy.py`, `_ie_open.js` (cópia do script
  validado + 3º argumento + escape não-ASCII). Testes em `tests/unit/client/launcher/`.
* **Expõe**: E inteiro (`close_all`, `open_terminal`, `attach_terminal`, `Launcher`). **Consome**: `Backend` (via
  `InProcessBackend`), `User32`/`FakeUser32`, `_win32`, `profiles`, `_types`.
* **Aceite**: `classify`: `BRAZIL_D - Pro Client` e `BRAZIL_D - Pro Client [D]` → `launcher`; `BRAZIL_DX - Pro Client`,
  `BRAZIL_D.xlsx - Excel` → ignoradas; `IEFrame` em `iexplore` filho de `msedge` → ignorada; HWND HLLAPI com título qualquer →
  instância; PID lançado sem janela principal → suas janelas; diálogo por dono; `sanitize` remove `Address: x`, URL, FQDN, IPv4;
  `close_all`: `evict` enviado antes do `WM_CLOSE` quando há worker; 2 instâncias → `post_close` nas duas; janela oculta
  recebe `SW_SHOWMINNOACTIVE` antes; sobrevivente com diálogo desconhecido → `post_close` do diálogo aos 3 s (nunca `click`
  afirmativo); "Short Name" → `click` no botão `OK`; prazo → `CloseFailed` sanitizado; nenhum método de matar processo usado;
  `open`: `reuse` (1 → conecta; 2 → fecha e abre com `notice`); launcher sai com 0 → continua; ≠ 0 → `LaunchFailed`; "Short Name"
  durante o registro → `MultipleInstances` + limpeza só das instâncias novas; `connect` rc 1 por 11 s e depois 0;
  `TerminalInUse`; `blank_first_screen(2)` → só devolve após a 1ª tela; `LaunchTimeout` com a fase certa; `minimized` aplicado
  cedo; `attach` com 0/1/2 instâncias; fallback IE com saída gravada (acento escapado) e `ok:false`; `_legacy`: servidor TCP
  0.1.5 falso recebe exatamente `ping` e `disconnect`; `shim:true` → nada; PID de outra sessão → intocado; timeout → "ocupado";
  sandbox recusa `Popen` do launcher/cscript e `post_close` real.

### P-D — Ponte, runtime 32-bit e doctor (Onda 2)

* **Arquivos**: `_bridge.py` (`BOOT`, `spawn`, `get_worker`, `handshake`, reciclagem), `_backend.py` (`WorkerBackend`),
  `_runtime32.py`, `_doctor.py`. Testes em `tests/unit/client/bridge/` e `tests/e2e/bridge/`.
* **Expõe**: C.3 (lado cliente), C.7, C.8, D.8, I, `doctor()`. **Consome**: P-B (`_ipc`, worker real), P-0.
* **Aceite** (e2e com worker real 32-bit e `--fake-dll`): spawn + handshake; 2º processo `owner` → `TerminalInUse` com PID/
  script; **worker de build antigo com dono vivo não é parado** e o cliente novo recebe `TerminalInUse`; quando o dono sai, o
  worker sai sozinho e o cliente novo sobe outro (R1/E2); build antigo sem dono → `shutdown` + novo spawn; `stopping` → espera;
  saídas 13/14/15/11 tratadas (corrida de 2 clientes → 1 worker); servidor de pipe com PID ≠ registro → `ChannelHijacked` sem
  laço; thread de cliente abandonada não prende o worker novo; `hang_on` em `WD_CopyPSToString` → `WorkerHung`, `read` repetido 1×
  após reciclar (hwnd igual), `send` **não** repetido e Session `uncertain`; reciclagem com hwnd diferente → `SessionReplaced`;
  Ctrl+C fecha a conexão e a próxima op reabre; PID reutilizado (`created` diferente) = morto; `_runtime32`: saídas gravadas de
  `py -0p` (dois formatos), globs, `real` de venv, pré-lançamento recusado, argumento inválido sem fallback; `doctor()` sem
  segredos e sem hostnames.

### P-E — API, receitas e compatibilidade (Onda 2)

* **Arquivos**: `session.py`, `recipes/{__init__,cics,ims}.py`, `compat.py`, `client.py`, `server.py` (stub), `api.py` (stub).
  Testes em `tests/unit/client/session/`, `.../recipes/`, `.../compat/`.
* **Expõe**: B.2–B.4, B.8, B.9, G.3–G.5 (na Session). **Consome**: P-A, `InProcessBackend` (B1), `FakeUser32`, `_launcher` (P-C;
  até o merge, testar `Session` pelo construtor).
* **Aceite**: estados da Session (B.3) — `uncertain` após `delivery` partial/unknown, `NoHostResponse` e Ctrl+C em `send`;
  saída por espera de conteúdo ou `acknowledge`; `replaced` por hwnd/evict/2ª `open`; `closed`; `press(ENTER)`: 2 snapshots
  estáveis antes, AID sozinha num `send` de 1 chunk, `pre` depois do texto, com `response_delay=0.5` o `read` seguinte vê a
  tela nova, com `lock_visible_after` usa `oia_after`; sem resposta → `NoHostResponse`; `send_keys("a<Enter>b<Enter>")`
  (T-SAFE-3); `fill` completa o campo, `align="right"`, `Truncated(none)`; `read` com `busy` repete até o prazo e levanta
  `HostBusy`; tabela "quem libera o quê"; `Cics.logon` feliz/expirada/inválida/tela estranha/campo de senha não oculto (nada
  escrito)/falha antes do ENTER (senha apagada, `screen=None`); erros testados antes de `ok`; `Ims.logon` com cenário IMS do
  fake; compat: cada método da tabela B.9.1 (retorno, prefixo `Comando '<acao>' falhou: `, textos legados de logon,
  `send_key("A@EB@E")` → 1 AID + WARNING, `copy_ps_to_string` com `nul_at`, `copy_field` com `nul_tail`, `auto_update=False`
  sem `RUMBAPY_DEV` ignorado, `connect` sem terminal → `TerminalNotOpen`, attach preguiçoso); `update_python_64bit` →
  `UpdateRequired`; stub `server.py` (ping `shim:true`, outra ação → erro, sai após a 1ª ação); `import rumbapy.api` → `ImportError`.

### P-G — Integração, segurança, ao vivo e documentação (Onda 3)

* **Arquivos**: `tests/e2e/` (fluxos completos: `attach`/`reuse` com worker real e cenário pré-registrado, `close` com dono vivo
  em outro processo, reciclagem), `tests/security/` (T-SEC-1 com espião no pipe, T-SEC-2, T-SAFE-1..3), `tests/live/`
  (`conftest.py` com `RUMBAPY_LIVE`, `RUMBAPY_GUARD`, `CONIN$`; roteiros L1–L9; relatório com `policy.audit`), `README.md`,
  `CHANGELOG.md`, atualização de `CLAUDE.md`, `ARCHITECTURE.md`, `INDEX.md`, `docs/KNOWN_ISSUES.md` (status de cada item).
* **Aceite**: toda a suíte verde na matriz; T-SEC/T-SAFE verdes; roteiros ao vivo **executados só dentro do permitido em J.6**,
  com relatório (tempos, rc, audit do guard, varredura de senha) e as âncoras de logon gravadas em `docs/interno/`; README
  com instalação em 1 comando, exemplo B.10, tabela de erros e comando de recuperação do update.

---

## M. Registro de decisões sobre as críticas

Legenda: **A** = aceito; **AP** = aceito com outra solução/parcial; **R** = rejeitado. Todo BLOQUEANTE (B) e MAIOR (M) tem
linha própria; os MENORES estão agrupados no fim.

### M.1 Robustez (R#)

| Item | Decisão | Motivo / onde |
|---|---|---|
| R1 (B) cliente pós-deploy derruba worker de outro script | A | dono antes do build (C.3); `shutdown`/evento só sem dono (C.6.3); worker sai sozinho no fim do lease com build novo (C.6.4); teste em P-D |
| R2 (B) handshake sem prazo / `WaitNamedPipeW(0)` | A | pipe e auth próprios com prazos e thread por conexão (C.1–C.2); `WaitNamedPipe(200)` em laço; esperas em fatias (D.8) |
| R3 (M) pipe previsível, DACL padrão | A | sufixo aleatório + DACL explícita (validada localmente) + conferência do PID do servidor (C.1) |
| R4 (M) `get_worker` sem estados de falha | A | máquina de estados com limites (D.8); idle pega o lock de spawn (D.4); PID + `created` (D.7) |
| R5 (M) lease preso num kernel esquecido | AP | `release`/`__exit__`/GC devolvem o lease (B.3); `open`/`close` revogam por `evict` (C.6.2). Preempção por inatividade: substituída pelo `evict` |
| R6 (M) `close_all` não garante fechar tudo | A | regra de prefixo + HWND do HLLAPI + PIDs lançados; diálogos por dono; desconhecido → `post_close` só no close (E.3–E.4) |
| R7 (M) launcher de instância única | A | `poll()==0` não é falha; instância nova por diferença de conjunto/HWND (E.5); L9 |
| R8 (M) rc 11 após `os._exit` | A | reconexão tolera rc 11 por 15 s com mensagem própria (C.8); L3b |
| R9 (M) watchdog/encerramento podem bloquear | A | temporizador de `os._exit` primeiro, locks com prazo, sem `logging.shutdown`, Disconnect/Reset na fila de T-dll (D.4) |
| R10 (M) colisão de `LOWORD(pid)` | A | checagem no boot, saída 15 e novo spawn (D.2 passo 6) |
| R11 (M) código velho+novo misturado | A | import ansioso + `BUILD_ID` duplo (A.6); spawn confere disco (D.8); worker calcula o próprio build (D.2) |
| R12 (M) corrida na `authkey.bin` | A | criação "se não existir" por `rename`; recriação só sob lock e sem workers vivos; 13 nunca recria (C.2) |
| R13 (M) filho da reexecução órfão | AP | reexecução cortada na v2.0 (H.5); Job Object fica para a v2.1 |
| R14 (M) senha em `Screen`/exceções | A | máscara no worker (D.6); `screen=None` no logon (B.7) |
| R15 (M) DEBUG com texto escrito | A | nenhum texto lido/escrito em log, em nível nenhum (D.7) |
| R16 (M) `open` com PS vazio | A | espera da 1ª tela (E.5 passo 7); `Screen.host_busy` |
| R17 (M) digitar em tela desconhecida após falha | A | estado `uncertain`/`replaced` (B.3); exemplo do README para no erro (B.10) |
| R18 (M) shim com `LocalBackend` | AP | shim vira stub sem DLL (B.9.4); não há tradutor de protocolo |
| R19 (M) testes ao vivo sem guard; fake duplicado | A | `guard.py` movido, `--guard` obrigatório ao vivo (J.6); `FakeEhllapi` reutilizado via `--fake-dll/--fake-path` (D.1) |

### M.2 Correção (C#)

| Item | Decisão | Motivo / onde |
|---|---|---|
| C1 (B) snapshot pós-AID antes da digitação | A | AID sozinha, `pre` após o texto, 2 snapshots estáveis, `oia_after`, sem `AID_GRACE` → `NoHostResponse` (G.3) |
| C2 (B) "teclas NÃO foram enviadas" | A | mensagens honestas + `delivery` none/partial/unknown com regra exata (B.7, D.5) |
| C3 (B) compat envia AIDs extras | A | `send_key` corta após a 1ª AID com WARNING (B.9.1); T-SAFE-2 |
| C4 (B) senha em campos ocultos vaza | A | máscara sempre no worker, enumeração com cache (D.6); logs sem conteúdo (D.7) |
| C5 (B) exceções ao update além do dev explícito | A | só `RUMBAPY_DEV=1`; checkout/editable → `UpdateFailed`; sem válvula offline (H.2, Q1) |
| C6 (M) desvios por `RUMBAPY_ROLE`/`UPDATED_TO`/`parent_process` | A | papel `worker` não existe (BOOT não roda `__init__`); sem reexecução; papéis ≠ user não operam (A.7) |
| C7 (M) segue HEAD e não master | A | `refs/heads/master` nas três fontes (H.3) |
| C8 (M) AUTORESET sem análise | A | `SetSessionParamEx("NORESET")` no connect (D.5); validação L1; Q4 |
| C9 (M) escrita parcial (rc 6) e exemplo que segue | A | pré-checagem de tamanho/proteção sem copiar (D.5); exemplo para no erro (B.10) |
| C10 (M) PAGE_UP/DOWN não são AID | A | entram em `AIDS`; teste de contrato com fake e guard (A.4) |
| C11 (M) âncoras fracas nas receitas | A | impressões digitais com ≥ 2 âncoras + campo oculto conferido; erros antes de `ok` (B.8); âncoras validadas no L6 |
| C12 (M) 2ª `open` redireciona Sessions antigas | A | identidade por HWND + `SessionReplaced` (C.6.5) |
| C13 (M) shim TCP sem autenticação | A | stub sem DLL que só avisa (B.9.4) |
| C14 (M) fallback IE diverge do validado | A | cópia do script validado (E.7) |
| C15 (M) fake/guard não reutilizados | AP | aceitos (a) e (c); (b) "guard sempre ligado em produção" **rejeitado**: `audit` cresce sem limite e a lista fechada de bindings (F.2) já impede funções perigosas |
| C16 (M) sessão segue após WorkerHung em op N | A | `uncertain` até espera de conteúdo ou `acknowledge` (B.3) |
| C17 (M) close com lease de outro / só por título | AP | `close` sem lease via `evict` + HWND HLLAPI + PIDs lançados (E.3–E.4); leitura da linha de comando do launcher **rejeitada** (frágil) |

### M.3 Engenharia (E#)

| Item | Decisão | Motivo / onde |
|---|---|---|
| E1 (B) commit ruim tranca todos | A | `_bootstrap` congelado com hash + `heal` + `try/except` no `__init__` (A.6, H.6) |
| E2 (B) update derruba worker em uso | A | igual a R1 (C.3, C.6) |
| E3 (B) testes podem tocar produção | A | `RUMBAPY_SANDBOX` respeitado pelo código de produção + sentinelas (J.2) |
| E4 (B) ao vivo sem `GuardedDll` | A | `--guard` obrigatório com `RUMBAPY_LIVE`, saída 16, políticas por teste (F.1, J.6) |
| E5 (M) fake não reutilizado | AP | reutilizado sem reescrever; **não** é movido para o pacote (é código de teste): carregado por `--fake-dll mod:fábrica --fake-path` (D.1) |
| E6 (M) pip morto no meio | A | pip desacoplado, sem kill, marcador `update-in-progress.json` força reinstalação (H.4) |
| E7 (M) cache de 60 s anula fail-closed | A | cache só com `up-to-date/updated` e remote == local == instalado (H.4) |
| E8 (M) variáveis vazam para filhos | A | `_restart_required` em variável de módulo; `UPDATED_TO` deixou de existir (H.4) |
| E9 (M) sem CI de instalação real | AP | `install-smoke` + proteção recomendada (J.1); estender válvula offline a `UpdateFailed` **rejeitado** (não há válvula) |
| E10 (M) verificação pós-pip em `--user` | A | verificação em interpretador novo (H.4) |
| E11 (M) checkout vira `unknown` | A | `.git` primeiro (→ erro sem `RUMBAPY_DEV`), distribuição casada por `locate_file`, `git rm` do egg-info (H.2) |
| E12 (M) build depende do PyPI/setuptools | A | backend PEP 517 in-tree, `requires = []` (H.7) |
| E13 (M) build_id mal ancorado | A | igual a R11 (A.6, D.2, H.10) |
| E14 (M) venv 32-bit: `Popen.pid` ≠ worker | A | sonda devolve o executável real; aceitação por pipe + `hello.pid` (I.1, D.8) |
| E15 (M) matriz sem 3.14/pymanager | A | matriz 3.12–3.14 × x64/x86, `pyright 3.12`, busca do pymanager (J.1, I.2) |

### M.4 Implementabilidade (I#)

| Item | Decisão | Motivo / onde |
|---|---|---|
| I1 (B) Backend ≠ OPS, DTOs sem módulo | A | `_types.py`, `OPS` em código, `Backend` espelho 1:1, `test_contracts` (C.4–C.5) |
| I2 (B) Session sem construtor | A | construtor + fábricas injetáveis (B.3) |
| I3 (B) regras de camada contraditórias | A | worker não usa `profiles` (launcher no cliente); `_update` pode usar `errors/_log/_locks`; `errors` só importa `screen` em `TYPE_CHECKING` (A.4) |
| I4 (B) interfaces internas do worker | A | `Hllapi`, `Engine`, `Dispatcher`, `User32`, `_win32` definidos (D.5, D.9, E.2, F.2) |
| I5 (B) ignora fake/guard; e2e não acha `tests/` | A | `--fake-dll`/`--fake-path`; guard movido (D.1, F.1) |
| I6 (B) `update_python_64bit` reexecuta | A | contexto `legacy` e reexecução cortada (B.9.2, H.5) |
| I7 (B) pós-AID libera cedo | A | igual a C1 (G.3) |
| I8 (M) disconnect × lease × with | A | `release`/`disconnect`/`close` e tabela "quem libera o quê" (B.3) |
| I9 (M) reciclagem ambígua; `attach` sobrecarregado | A | op `connect`, `hello_ack`, reciclagem = spawn → handshake → connect → repetir (C.8) |
| I10 (M) lease impede `close_terminal` | A | `close_all` no cliente + `evict` (E.4) |
| I11 (M) `sessions()`/`doctor()` sem worker | AP | `sessions()` cortado; `doctor` usa a letra reservada `_` (A.5) |
| I12 (M) build_id/BOOT no worker | A | BOOT sem `__init__`, build calculado no boot, saída 14 (D.1–D.2) |
| I13 (M) exports preguiçosos | A | import ansioso, sem `__getattr__` (A.6) |
| I14 (M) PageUp/PageDown | A | igual a C10 |
| I15 (M) promessa de credenciais | A | igual a C4/R15 + mensagens `secret` (B.7) |
| I16 (M) janela rouba foco | A | `SW_SHOWNOACTIVATE`/`SW_SHOWMINNOACTIVE`; `hide`/`front`/`hidden` cortados (E.6) |
| I17 (M) prazos incoerentes | A | `client_timeout` por op > watchdog + 5 s (C.4); divisão do `open` (E.5) |
| I18 (M) regra `ie` atinge Edge | A | `parent_image != msedge.exe`; Edge só por letra presa (E.3) |
| I19 (M) compat muda semântica | A | decisão por linha (B.9.1, Q3); `nul_at`/`nul_tail` preservam retornos |
| I20 (M) exceções ao update | A | igual a C5 |
| I21 (M) trilhas sem dono | A | P-0 de contratos + ondas + aceite por pacote (L) |
| I22 (M) worker como proxy fino | A | janelas/launcher no cliente; ops curtas; sem `cancel` (A.1, C.4, E) |
| I23 (M) cortar reexecução | A | H.5 |
| I24 (M) API grande demais | A | cortes de K.4; mantidos `find` (cliente), `table`, `_repr_html_` |
| I25 (M) shim mínimo | A | B.9.4; `LocalBackend` removido |
| I26 (M) adiar embeddable | A | I.3; busca reduzida (I.2) |

### M.5 Menores (agrupados)

| Itens | Decisão | Onde |
|---|---|---|
| R20 EOF cancela jobs; R21 suspensão; R23 arquivos de estado; R27 neutralização; R28 inibição de operador; R29 queda de host; R30 lixo de lançamento; R31 janela fechada à mão; R32 close logado; R33 sanitização/logoff; R35 resync | A | C.6.4, D.4, A.5, E.8, G.3, G.2, E.3/E.5, D.4, L6, E.3/B.7, D.3 |
| R22 janela oculta para sempre | AP | `hidden` cortado; oculta → minimizada antes do close (E.4) |
| R24 mutex `Local\` entre sessões | AP | locks de arquivo por usuário (A.5) em vez de `Global\` |
| R25 troca de diretório do embeddable | — | provisionamento adiado (I.3) |
| R26 `RESTART_REQUIRED` herdado | A | variável de módulo (H.4) |
| R34 fechar sem GitHub | AP | só mensagem orientando fechar pelo X (B.7); sem caminho que pule o update |
| C18 1ª tela/rc 4; C19 `SearchPS`/SRCHALL; C20 `error_checks` no `wait_ready`; C21 vazamentos menores; C22 hostname; C23 diálogos; C25 L4/MDT; C26 mistura por import; C27 pipe; C28 textos da compat; C29 fatos; C30 Ctrl+C entre chunks; C31 `reuse=True` | A | E.5, C.4 (op `search` removida), G.3/G.5, B.7/D.3/D.7, E.3, E.3–E.4, J.6, A.6, C.1, B.9.1, B.5/F.4/E.3, C.7, B.2 |
| C24 janela oculta/estado persistido | AP | `hidden` cortado; restauração antes do `WM_CLOSE` (E.4) |
| E16 papel `cli`; E17 `notice` no `_update`; E18 `__getattr__`; E19 BOOT; E20 DLL antes do pipe; E21 commit no worker; E22 ctypes; E23 `TimeoutError` é `OSError`; E24 `Key(str, Enum)`; E25 `pythonw`; E27 mutex/Ctrl+C; E28 URL de archive; E29 `.gitignore`; E30 `CONIN$`; E31 `ie_open.js`; E32 0.1.5 em paralelo; E33 outra sessão; E35 boot/log disputados; E37 cache de proxy/UA; E39 import durante pip | A | A.7, H.6, A.6, D.1, D.2, D.1, D.9, C.8, B.6, H.6, D.8/A.5, H.2, H.7, J.6, E.7, H.8, E.8, A.5/D.7, H.3, H.6 |
| E26 detalhes da reexecução; E34 shim; E36 TARGET quebrado | — | não se aplicam (reexecução, shim completo e provisionamento cortados) |
| E38 `RUMBAPY_DEV` esquecido | AP | `notice` a cada import; lista `DEV_USERS` rejeitada (complexidade sem ganho) |
| I27 ops redundantes; I28 `cancel`; I29 docstring; I30 `Key` enum; I31 `Python32NotFound`/`ValueError`; I32 `WindowInfo.kind`; I33 `doctor` fora de `__main__`; I34 `open` fora de `__all__`; I35 retornos das esperas; I36 tabela de rc; I37 ordem pipe/DLL; I38 dica de `MultipleInstances`; I39 hostname; I40 sem `Tn3270Backend`; I41 `fill` nas receitas; I42 `_codec` no topo; I43 locks de arquivo; I44 operador × host; I45 Sessions velhas; I46 eventos; I48 portão de spec | A | C.4, C.7, B.2, B.6, B.7, E.6, A.2, A.6, B.3/G, F.6, D.2, B.7, E.3, C.5, B.8, A.2, A.5, G.3, C.6, C.3, L (P-0 + respostas de K.2) |
| I47 `rumbapy.commit` | AP | atributo removido; commit exibido por `python -m rumbapy version` |

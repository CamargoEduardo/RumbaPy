# CLAUDE.md — guia para agentes neste repositório

RumbaPy é uma biblioteca Python que automatiza terminais **3270 (CICS/IMS) do Micro Focus RUMBA
Web-to-Host** da Volvo usando a DLL 32-bit `ehlapi32.Dll` (EHLLAPI). O usuário roda scripts em Python
**64-bit**; a DLL só carrega em **32-bit**, então cada letra de terminal tem um **processo auxiliar
(worker) 32-bit** que roda a partir dos arquivos do pacote 64-bit. Versão atual: **2.0.0** (branch `dev`).
Comece por [`INDEX.md`](INDEX.md). A especificação normativa da v2 é
[`docs/ESPECIFICACAO_V2.md`](docs/ESPECIFICACAO_V2.md) (as tabelas A.8–A.14 prevalecem sobre o texto original).

## Regras invioláveis

1. **O auto-update obrigatório deve ser mantido.** O usuário final tem que rodar sempre a última
   versão (requisito de negócio do dono do projeto). Nunca remova, desligue por padrão ou enfraqueça
   `_update.ensure_latest` (chamado no `import`, no `open`/`attach`/`RumbaClient()`, no CLI e por
   `client.update_python_64bit`), o `heal` do `_bootstrap`, a conferência de `build_id` entre cliente e worker,
   nem o bloqueio sem rede (`UpdateCheckFailed`) sem colocar no lugar algo que garanta o mesmo — ou mais
   (`docs/ESPECIFICACAO_V2.md` §H). A única exceção é `RUMBAPY_DEV=1`, explícita, logada e avisada a cada import.
2. **Push no `master` = deploy imediato para todos os usuários.** O auto-update compara o commit
   instalado com o `refs/heads/master` do GitHub. Não faça commit/push sem pedido explícito; nunca
   empurre código quebrado ou experimental para o `master`. Portão do `master` (J.1): CI verde em `dev`, roteiro
   ao vivo L1–L6 executado pelo dono, versão incrementada, `CHANGELOG.md`, teste manual do `heal` e `install-smoke`
   verde no GitHub Actions.
3. **Compatibilidade da API pública.** Scripts de usuários usam `RumbaClient(terminal_type,
   python32bit_path=None, auto_update=True)` e os métodos `logon_cics`, `logon_rhelp`, `send_key`,
   `copy_ps_to_string`, `copy_string_to_ps`, `copy_field`, `screen_load`, `close_terminal`,
   `disconnect`, `connect` (hoje em `compat.py`, reexportados por `rumbapy` e `rumbapy.client`). A API v2
   (`rumbapy.open/attach/close`, `Session`, `Screen`, `Key`, exceções de `errors.__all__`) também é pública.
   Não mude nomes/assinaturas/semântica sem camada de compatibilidade e sem registrar no `CHANGELOG.md`.
4. **Credenciais:** nunca commitar, nunca logar, nunca pôr em mensagem de exceção (`config.ini` está no
   `.gitignore`; logon recebe `uid`/`pwd` por parâmetro; senha sempre com `secret=True` e em campo oculto).
5. **O repositório GitHub é PÚBLICO.** Nunca versione dados internos da Volvo (hostnames/IPs de
   mainframe, portas, situação de TLS, o endereço do diálogo "Connecting To Address", nomes de regiões/sistemas,
   chaves `kc1..kc4`/`DevID` dos perfis, telas com dados de negócio). Eles ficam em `docs/interno/` (no
   `.gitignore`); nos docs públicos use apelidos ("host CICS-BR") e `<portal>`. Antes de terminar qualquer mudança
   em docs, varra os arquivos versionáveis (`git ls-files -co --exclude-standard`) com os padrões de
   `docs/interno/tsec2_patterns.txt` (sem mostrar os padrões em lugar nenhum).
6. **A DLL é 32-bit e precisa ser a mesma instalada pelo RUMBA**
   (`C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll`). A seção compartilhada `.SHARDAT`
   só funciona com o mesmo arquivo. Não copie a DLL para outro lugar.

## Armadilhas que já custaram caro (leia antes de mexer em `_worker/*`, `_launcher.py`, `_bridge.py`)

* A conexão HLLAPI é **por processo**, não por objeto nem por `hwnd` (`docs/RUMBA_EHLLAPI.md` §4): daí um worker
  por letra, uma única thread de DLL (T-dll) e nada de `MultiThreadConnectionOn()` (cada thread gastaria um slot).
* Chamadas à DLL podem **bloquear para sempre** (`WaitForSingleObject(INFINITE)` dentro da DLL).
  A saída robusta é o watchdog do worker reciclar o processo com `os._exit()` (`_worker/watchdog.py`: 15 s no
  `WD_ConnectPS`, **8 s** no `WD_QuerySessions`, 5 s no encerramento, 20 s nas demais).
  **Nunca** `TerminateProcess`/`taskkill /F` no processo que carregou a DLL (vaza a tabela global de
  32 clientes → popup modal "Exceeded maximum number of clients, reboot for correction").
* **`WD_QuerySessions` trava logo depois do fechamento de um emulador** (achado ao vivo, A.14 l/m): mesmo com o
  processo do emulador já saído, a conferência travou (intermitente). Por isso o `close_all` espera a saída dos
  processos **sem chamar a DLL** (`PROCESS_EXIT_S` = 5 s) e dispensa a consulta quando a instância que o worker
  conhecia foi fechada e o processo dela saiu.
* **Fechar um terminal derruba a conexão HLLAPI dos outros** (L9, A.14 n): o worker do outro terminal recebe rc 1
  na próxima chamada; ele faz `reconnect_same()` (mesma letra, mesmo hwnd) e repete a op **uma vez** — só leitura, ou
  entrada cuja 1ª chamada foi recusada. Abrir não derruba. Nunca repita entrada parcial.
* **O processo do Web Launcher tem uma `Internet Explorer_Hidden` com `WS_VISIBLE` e sem título** (além de "RUMBA
  API Manager", "Telnet3270 Application", janelas IME/GDI+/.NET): `WM_CLOSE` só na janela principal
  (`<PERFIL> - Pro Client`, classe `WindowsForms10.*`); as auxiliares estão em `_instances.HELPER_CLASSES`.
* **Duas instâncias da mesma letra** → modal "RUMBA API Service Control — Short Name already in use" e o HLLAPI fica
  na 1ª. Com esse modal aberto, `WM_CLOSE` derrubou o launcher com **0xC0000005**: o `close` clica OK antes do
  `WM_CLOSE` da dona; a 2ª instância às vezes ainda cai ao fechar (defeito do RUMBA, sem efeito nas outras).
* **Um processo de launcher por perfil** (L9): `BRAZIL_D` e `BRAZIL_Z` ficam em processos diferentes.
* Texto da DLL é **ANSI do Windows (`cp1252`)**, não UTF-8. `buffer.value` corta no primeiro `NUL`; use `.raw[:n]`
  (`_codec.py` centraliza isso; NUL → espaço, NFC na escrita).
* `WD_SendKey` rejeita ≥ 256 bytes (rc=2); o manual recomenda ≤ 128 (`keys.encode_body` faz chunks ≤ 128).
  **Tudo após a 1ª tecla AID (Enter/PFn/PAn/Clear) é descartado** — uma AID por chamada, sempre no último chunk.
  `@` literal é `@@`.
* `WD_Wait` == 0 só diz que o teclado aceita digitação, **não** que a próxima tela chegou: espere por texto/cursor
  (`waits.py`). Logo depois do `ConnectPS` rc 0 o PS pode estar vazio (rc 4).
* `WD_ShowSession` não minimiza a janela do launcher: use `user32.ShowWindowAsync` no HWND (nunca ativar/focar).
* Chame `WD_ResetSystem` antes de encerrar o processo que usa a DLL (parada limpa do worker).
* IMS A tem **32×80** (modelo 3); BRAZIL D/Z têm 24×80. Posições lineares usam as colunas da sessão (`_codec`).
* Só existem sessões HLLAPI se o terminal foi aberto por um perfil HLLAPI (`index_pro_api.html` / `profpro/<PERFIL>`
  pelo launcher); o atalho do usuário abre `index_pro.html`, **sem** HLLAPI.
* Registro da letra leva ~9–25 s e a conexão ~11–27 s depois de lançar; rc 1 no `ConnectPS` enquanto o diálogo
  "Connecting To Address" está na tela é normal.
* Conteúdo de campo oculto nunca atravessa o pipe (`_worker/mask.py`); `secret=True` num campo **visível** só
  protege a leitura até a resposta da próxima AID (A.9 h).
* Servidores 0.1.5 ainda vivos (portas 8500/8600/8700/8800) seguram a conexão HLLAPI da letra: `_legacy.neutralize`
  manda `disconnect` (nunca mata o processo).

## Layout (v2)

```
src/rumbapy/
  __init__.py      forma literal da A.6: update no import, imports ansiosos, BUILD_ID, heal em qualquer falha
  __main__.py      CLI: python -m rumbapy {doctor | close <L> | version | update}
  _bootstrap.py    CONGELADO (hash em _build/bootstrap.sha256): rede/pip/verificação/heal, só stdlib
  _update.py       política do auto-update (ensure_latest, install_mode, estado, TTL)
  _role.py         papel do processo: user | cli | legacy-shim
  _version.py      __version__, PROTOCOL, URLs do repositório
  _buildid.py      hash dos arquivos do pacote (mesmo código nos dois lados)
  _paths.py        HOME (%LOCALAPPDATA%\rumbapy ou RUMBAPY_HOME), nomes de pipe/evento/locks/registros, atomic_write
  _locks.py        file_lock (msvcrt.locking)
  _log.py          formatos, setup_client_logging, notice() (stderr curto), SafeRotatingFileHandler
  _codec.py        cp1252, NUL→espaço, NFC, conjunto seguro, pos <-> (linha, col)
  _protocol.py     OPS (tabela das operações), limites, validate_request, encode/decode
  _types.py        Profile, OpenInfo, WindowInfo, SessionEntry, SessionStatus, ConnectResult, ReadResult...
  _ipc.py          named pipe com DACL + autenticação HMAC + framing JSON (cliente e worker)
  _win32.py        kernel32/advapi32/iphlpapi + 3 funções user32 do worker (ctypes.WinDLL privado)
  _user32.py       Protocol User32 + RealUser32 (respeita o sandbox; lê títulos com InternalGetWindowText)
  _instances.py    puro: classifica janelas em instâncias do perfil, diálogos, sanitize()
  _launcher.py     open/attach/close_all no cliente (Web Launcher, fallback IE, limpeza)
  _legacy.py       neutralização de servidores 0.1.5
  _ie_open.js      script JScript validado ao vivo (fallback IE)
  _bridge.py       WorkerHandle, get_worker, spawn (BOOT), handshake, reciclagem, tradução de erros de transporte
  _backend.py      Protocol Backend (espelho 1:1 de OPS) + WorkerBackend
  _runtime32.py    descoberta/validação do Python 32-bit
  _doctor.py       doctor(): relatório em português (imprime)
  errors.py        hierarquia RumbaError (templates/hints, to_wire/from_wire)
  keys.py          Key, AIDS, tokenizadores, split_at_aids, encode_body
  screen.py        Screen, Field, Cursor, OIA (puro)
  waits.py         motor de espera puro (wait_until) e constantes
  profiles.py      PROFILES D/Z/A, resolve(), launcher_arg(), index_url()
  session.py       API pública: open/attach/close e Session
  recipes/         cics.py (Cics.logon, start_transaction, check), ims.py (Ims.logon)
  compat.py        RumbaClient (API 0.1.5) sobre a Session
  client.py        módulo legado (reexporta RumbaClient, Mnemonic, funções legadas)
  server.py        stub para clientes 0.1.5 em memória (sem DLL)
  api.py           stub: ImportError explicativo
  _worker/         lado 32-bit (nunca executa rumbapy/__init__.py; sobe pelo BOOT de _bridge)
    main.py        argv, ordem de boot, códigos de saída
    server.py      pipe, threads (T-accept, T-conn, T-dll, T-watch), lease, fila, evict, eventos
    dispatch.py    handlers das ops, validação por OPS, checagem de hwnd, reconexão transparente
    engine.py      estado HLLAPI da sessão, regras de delivery, reconnect_same
    hllapi.py      load_dll() e a classe Hllapi (bindings WD_*)
    rc.py          rc -> exceção
    structs.py     parse de QuerySessions/QuerySessionStatus (puro)
    mask.py        máscara de campos ocultos e spans secretos (puro)
    watchdog.py    T-watch: limites por função, janela sumida, idle, parada
    guard.py       GuardedDll (intertravamento dos testes ao vivo)
_build/            backend PEP 517 só stdlib (rumbapy_build.py) + bootstrap.sha256
tests/             conftest (sandbox), fakes/, unit/, e2e/ (worker real com --fake-dll), live/ (manual)
tools/research/    sondas de engenharia reversa e roteiros ao vivo (não empacotados)
docs/              referência técnica, especificação, problemas conhecidos, plano
```

Regras de camada (verificadas por `tests/unit/test_layering.py`): `_worker/*` só importa stdlib e
`_worker.*`, `_protocol`, `_ipc`, `_codec`, `_buildid`, `_win32`, `_paths`, `_log`, `_locks`, `_types`, `errors`, `keys`;
`_bootstrap` só stdlib; ninguém usa `ctypes.windll`/`cdll`; nenhum `print` fora de `__main__`, `_log.notice`,
`_bootstrap.say` e `_doctor.doctor`; nada de `pickle`.

## Ambiente de desenvolvimento

* Windows com RUMBA instalado pelo portal (`<portal>`; a URL real é `profiles.PORTAL_DEFAULT`).
* Dois Pythons: x64 em `C:\Codes\RumbaPy\.venv\Scripts\python.exe` e x86 em `C:\Codes\RumbaPy\.venv32\Scripts\python.exe`
  (ambos com `pytest` e `pytest-timeout`); `ruff` e `pyright` em `.venv\Scripts\` (`ruff.exe`, `pyright.exe`; fora do `PATH`). `py -0p` lista os instalados.
* **Testes nunca tocam produção**: o `tests/conftest.py` liga `RUMBAPY_SANDBOX=1`, `RUMBAPY_DEV=1`,
  `RUMBAPY_QUIET=1` e `RUMBAPY_HOME=<temp>` antes de importar o pacote, e sentinelas derrubam qualquer tentativa de
  carregar a DLL, mandar mensagem a janelas reais, abrir RUMBA/launcher/IE/cscript ou matar processos.
* Comandos (rode nos dois interpretadores):
  ```
  .venv\Scripts\python.exe   -m pytest tests -m "not live" -q
  .venv32\Scripts\python.exe -m pytest tests -m "not live" -q      (os e2e com worker 32-bit real rodam aqui)
  .venv\Scripts\ruff.exe check src tests _build --select E,F,W --ignore E501
  .venv\Scripts\pyright.exe                                        (basic, pythonVersion 3.12; bloqueante no CI)
  RUMBAPY_SMOKE=1 ... pytest tests/e2e/update/test_install_smoke.py (install-smoke: zip do HEAD + GitHub falso local)
  ```
  Não há Python 3.12/3.14 locais: garanta compatibilidade com 3.12 por inspeção (o CI roda 3.12/3.13/3.14 × x64/x86).
* Qualquer execução manual de código do pacote fora do pytest: `RUMBAPY_SANDBOX=1`, `RUMBAPY_DEV=1` e
  `RUMBAPY_HOME=<pasta temporária>`.
* Dublês a reutilizar (não recriar): `tests/fakes/fake_ehllapi.py` (`FakeEhllapi`), `fake_user32.py`,
  `scenarios.py` (fábricas para `--fake-dll`), `fake_backend.py` (`InProcessBackend`), `rumbapy._worker.guard`
  (`GuardedDll`). Injeção: `session._set_factories(...)`, `_launcher.Launcher(user32, get_backend, ...)`.
* `tools\research\probe_hllapi.py` **não é inofensivo**: carrega a `ehlapi32.Dll` REAL (sem `GuardedDll`) e chama
  `WD_QuerySessions`, que pode travar dentro da DLL (A.14 l/m). Exige `RUMBAPY_LIVE=1` e a confirmação `SIM` no
  console; só o agente principal ou o dono o roda, com autorização, nunca logo depois de fechar um emulador, e
  **nunca** se mata o processo se ele travar (o watchdog da sonda sai com `os._exit`). `dde_list.py` faz broadcast de
  `WM_DDE_INITIATE` e abre conversas DDE com janelas reais: mesma regra (`enum_windows.py` só enumera; ver
  `tools/research/README.md`).
* Workers em execução ficam ociosos 30 min e saem sozinhos. Para trocar o lado 32-bit durante o desenvolvimento,
  feche os scripts/kernels que usam a letra (fim do lease): no próximo uso o `build_id` diferente faz o cliente
  reciclar o worker sem dono. **Não** use `python -m rumbapy close <L>` para isso: ele só revoga o lease (`evict`)
  e manda `WM_CLOSE` às janelas do perfil — fecha o terminal (logado) e o worker continua vivo até o idle. O `close`
  é ação ao vivo: só o agente principal, com autorização do dono; subagentes nunca.

## Testes ao vivo (terminal de PRODUÇÃO)

* **Só o agente principal**, com autorização do dono, e só dentro do permitido na tabela J.6 da especificação
  (L1–L6, L3b, L8, L9 permitidos; L7 IMS e qualquer PF/PA/transação/escrita em tela de negócio exigem plano aprovado).
  Subagentes e pacotes paralelos **nunca** executam nada que carregue a DLL real, abra o RUMBA, o launcher, o
  `cscript`/IE, mande mensagens a janelas reais ou mate processos.
* Pré-condições: branch `dev`, `RUMBAPY_LIVE=1`, guard por roteiro `readonly|text|login` repassado ao worker (que
  recusa subir sem guard: saída 16; um `RUMBAPY_GUARD` do dono é teto e pula roteiros que precisam de mais),
  confirmação `SIM <letra>` digitada no console (`CONIN$`; nenhuma variável de ambiente confirma; sem console, pulado),
  credenciais só digitadas (`getpass`), relatório com o registro do guard tirado do log do worker (linhas `op=` de
  escrita/AID/conexão com `rc`, AIDs conferidas por roteiro, NORESET) e varredura de senha nos logs (achou: só o
  local, com `***`); o `RUMBAPY_HOME` temporário é apagado no fim.
* Ferramentas: `tools/research/live_v2_probe.py --script l1|l2|l3|l3b|l4|l5|l6|l8|l9|compat` (roteiros da API v2,
  código do `HEAD` exportado por `git archive`) e os invólucros `live_worker_probe.py` (l1/l4), `live_l3b_probe.py`,
  `live_session_probe.py` (l5), `live_close_probe.py`, `live_legacy_probe.py` (l8) — toda sonda `live_*` passa pelo
  executor, nenhuma carrega a DLL por conta própria; `tests/live/` (`set RUMBAPY_LIVE=1` e
  `.venv\Scripts\python.exe -m pytest tests/live -m live -p no:randomly -x -rs`; cada roteiro roda em processo próprio
  via `python -m tests.live.runner`, com confirmação `SIM <letra>` pelo console). Relatórios vão para
  `docs/interno/referencia/` (não versionado).

## Convenções do código

* Identificadores em inglês; mensagens, logs e comentários em **português**.
* `logger = logging.getLogger(__name__)` por módulo; sem `print` (exceções da regra de camada acima).
* Imports: primeiro `from x import y`, depois `import x`, **cada bloco ordenado por comprimento
  decrescente da linha** (ex.: `import subprocess` antes de `import json`). Como o isort do ruff não expressa isso,
  arquivos novos começam com `# ruff: noqa: I001`; E/F/W continuam checados.
* `from __future__ import annotations`; nada de sintaxe posterior ao 3.12.
* Toda falha é subclasse de `errors.RumbaError`, criada por `Classe.make(variante, **campos)` com os textos de
  `templates`/`hints`; o worker devolve `{"ok": false, "error": to_wire(exc)}` e o cliente reconstrói com `from_wire`.
  Mensagens nunca citam credenciais nem conteúdo de tela; textos de janela passam por `_instances.sanitize`.
* Coordenadas públicas são `(row, col)` = (linha, coluna), 1-based.
* Mudou um contrato? Edite `docs/ESPECIFICACAO_V2.md` no mesmo commit. Versão em `pyproject.toml` e
  `_version.py` (o backend confere) é incrementada a cada mudança publicada, com entrada no `CHANGELOG.md`.

## Documentação

| Arquivo | Conteúdo |
|---|---|
| `INDEX.md` | Mapa de tudo (comece aqui) |
| `README.md` | Instalação e uso para o usuário final |
| `CHANGELOG.md` | O que mudou em cada versão (inclui as diferenças de comportamento da compat) |
| `ARCHITECTURE.md` | Arquitetura v2, fluxos e por que é assim |
| `docs/ESPECIFICACAO_V2.md` | Especificação normativa da v2 (contratos, constantes, testes, pacotes) |
| `docs/RUMBA_EHLLAPI.md` | Referência da `ehlapi32.Dll`: 58 exports, assinaturas, IPC, threads, rc, mnemônicos, medições ao vivo |
| `docs/RUMBA_AMBIENTE.md` | Instalação RUMBA, perfis do portal, processos/janelas, COM, caminhos de automação |
| `docs/KNOWN_ISSUES.md` | Status dos problemas da 0.1.5, problemas atuais e pendências do dono |
| `docs/PLANO_DE_MELHORIAS.md` | Plano original (histórico) e o que foi feito/adiado |
| `tools/research/README.md` | Como reproduzir a engenharia reversa e os roteiros ao vivo |
| `docs/GLOSSARIO.md` | Jargão de mainframe/3270/RUMBA e termos da v2 (worker, lease, evict…) |
| `docs/interno/` (não versionado) | Hosts reais; achados ao vivo (`referencia/live_findings_*.md` e relatórios JSON); `referencia/tlb` e `referencia/dotnet`; `referencia/portal`; `referencia/fontes` (**manual "WD_API Language Reference"**); `design/` (proposta e críticas da v2) |

Mantenha esses arquivos atualizados quando mudar comportamento: são a memória do projeto.

# Arquitetura do RumbaPy (v2.0.0)

> Documento de referência para humanos e agentes: **como a v2 funciona e por que ela é assim**. Contratos exatos
> (constantes, mensagens, protocolo) estão na especificação normativa [`docs/ESPECIFICACAO_V2.md`](docs/ESPECIFICACAO_V2.md)
> (as tabelas A.8–A.14 prevalecem). O funcionamento interno do RUMBA está em [`docs/RUMBA_EHLLAPI.md`](docs/RUMBA_EHLLAPI.md)
> e [`docs/RUMBA_AMBIENTE.md`](docs/RUMBA_AMBIENTE.md); problemas e pendências em [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).
> A arquitetura da 0.1.5 (servidor TCP + pywin32/pywinauto) está no histórico do Git (`git show 5c03d47:ARCHITECTURE.md`).

---

## 1. Propósito

Automatizar, a partir de Python 64-bit, sessões de terminal **3270 (mainframe IBM)** abertas pelo
**Micro Focus RUMBA Web-to-Host Pro 6.9.4** da Volvo:

| Short name | Perfil W2H | Sistema | Host TN3270 | Tela |
|---|---|---|---|---|
| `D` | `BRAZIL_D` | CICS Brasil | host CICS-BR | 24×80 (modelo 2) |
| `Z` | `BRAZIL_Z` | CICS Brasil (2ª sessão) | host CICS-BR | 24×80 (modelo 2) |
| `A` | `IMS_A` | IMS / RHELP | host IMS-SE | **32×80 (modelo 3)** |

> Endereços reais ficam em `docs/interno/` (não versionado — o repositório é público).

O usuário é um analista que escreve scripts (pandas, Jupyter) que leem e escrevem em telas do CICS/IMS. Objetivo:
"`import rumbapy` e pronto", sempre na última versão, sem nunca mandar ao host algo que não foi pedido.

---

## 2. Visão geral

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ Script do usuário (Python 64-bit)   rumbapy.open("D") / RumbaClient("D")               │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ __init__  ─ _update.ensure_latest (auto-update obrigatório) ─ BUILD_ID ─ heal            │
│ session.Session ── waits (motor puro) ── recipes (cics/ims) ── compat.RumbaClient        │
│ _launcher (abrir/anexar/fechar; user32 via _user32/_instances; _legacy) ── _doctor       │
│ _backend.WorkerBackend ── _bridge (get_worker, spawn, handshake, reciclagem)             │
├──────────── named pipe por letra: DACL só do usuário + HMAC mútuo + JSON (_ipc) ────────┤
│ worker 32-bit por letra (Python 32-bit, -I -S, BOOT sem rumbapy/__init__)               │
│   T-accept ─ T-conn (auth, handshake, lease, validação) ─ fila ─ T-dll (única) ─ T-watch │
│   dispatch → engine → mask (campos ocultos) → hllapi (ctypes) [→ GuardedDll ao vivo]     │
├────────────────────────────── ctypes stdcall, 32-bit ───────────────────────────────────┤
│ C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll   (.SHARDAT compartilhada)  │
├──────────────── IPC Win32: memória "HLLAPIMem<id>" + PostMessage + evento ──────────────┤
│ Processo "Host Access Web Launcher.exe" (um por perfil) → WebBrowser (motor IE)          │
│   ProContainer.ocx ─ WdMfDsp.ocx (display 3270) ─ WDApiSvc.ocx (servidor HLLAPI)        │
├──────────────────────────────────── TN3270 ─────────────────────────────────────────────┤
│ Mainframe IBM z/OS (CICS / IMS)                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

Fronteiras:
* **Só o worker carrega a DLL.** Cada op do worker é curta (≤ 1 chamada de cada `WD_*`, salvo `snapshot`, `read_field`
  e `send`). Janelas, launcher, fallback IE e `close_all` rodam no **cliente 64-bit** (ctypes `user32`/`kernel32`).
* A `Session` fala só com um `Backend` (`_backend.py`), espelho 1:1 da tabela `_protocol.OPS`. Os testes trocam o
  backend por `InProcessBackend` (worker no mesmo processo com a `FakeEhllapi`).
* Núcleo puro e testável: `_codec`, `keys`, `screen`, `waits`, `_instances`, `_worker/structs`, `_worker/mask`,
  `_buildid`, decisão de update.

---

## 3. Por que é assim (restrições que moldaram o desenho)

1. **A DLL é 32-bit e não existe versão 64-bit instalada** (W2H Pro 6.9.4; o `EHLAPI64.DLL` só existe no Rumba+
   Desktop 10.2 SP1+). O usuário quer o ecossistema 64-bit → **cliente 64-bit + processo 32-bit**.
2. **A conexão HLLAPI é por processo** (o ID do cliente é `LOWORD(PID)`), e só um cliente por PS sob `WRITE_SUPER`
   (rc 11) → **um worker por letra**, uma única thread de DLL, sem `MultiThreadConnectionOn`.
3. **A DLL espera sem prazo** (`WaitForSingleObject(INFINITE)`) e matar o processo vaza a tabela global de 32 clientes
   → **watchdog** que encerra o worker com `os._exit` (o `DllMain` limpa) e um novo sobe na próxima op.
4. **O usuário precisa rodar sempre a última versão** (requisito de negócio) e não há PyPI → auto-update obrigatório
   pelo `master` do GitHub, com `_bootstrap.py` congelado e `heal` para sobreviver a um commit quebrado.
5. **Instalar pacotes no Python 32-bit era frágil** (atualização dupla, versões diferentes nos dois lados) → o worker
   roda **a partir dos arquivos do pacote 64-bit** (`-I -S` + BOOT) e cliente/worker conferem o mesmo `build_id`.
6. **Máquinas compartilhadas (VDI/RDP)** → named pipe com DACL do usuário, nome aleatório, autenticação HMAC mútua
   e conferência do PID do servidor do pipe (nada de socket TCP aberto).
7. **ActiveX só roda no motor do IE** e a automação COM do IE está aposentada → o terminal é aberto pelo **Host
   Access Web Launcher** (`hawlauncher://`), que hospeda o motor do IE num processo próprio; o IE fica de reserva.
8. **Duas instâncias da mesma letra quebram o HLLAPI** ("Short Name already in use") → `open`/`close` fecham
   **todas** as instâncias do perfil, mas só janelas que sejam sessões RUMBA daquele perfil.
9. **Senhas e dados de tela não podem vazar** → campos ocultos são mascarados **dentro do worker**; nada de texto de
   tela ou digitado em log, em nível nenhum; mensagens sanitizadas.
10. **Testes não podem tocar produção** → sandbox imposto pelo código de produção (`RUMBAPY_SANDBOX=1`) + sentinelas no
    `conftest.py`; ao vivo, só com `GuardedDll` e autorização do dono.

---

## 4. Componentes (cliente 64-bit)

| Módulo | Papel |
|---|---|
| `__init__.py` | Forma literal da A.6: detecta o papel (`_role`), roda `ensure_latest("import")`, calcula `BUILD_ID`, importa **todos** os módulos do cliente (ansioso), compila sem executar os módulos do `_worker` (e o `__main__` no papel `cli`), recalcula o `BUILD_ID` (mudou → `UpdateRequired`), liga o log do cliente. Qualquer falha que não seja `RumbaError` vai ao `_bootstrap.heal`. |
| `_role.py` | `user` (scripts, Jupyter), `cli` (`python -m rumbapy`), `legacy-shim` (`python -m rumbapy.server`, sem update). |
| `_bootstrap.py` / `_update.py` | Mecanismo (rede, pip, verificação, heal; congelado) e política (quando checar, modo de instalação, cache, trava, marcador de update em curso). §7. |
| `session.py` | `open`/`attach`/`close` e a `Session`: estado `ok/uncertain/replaced/closed`, prazos, esperas antes de tecla e depois de AID, `error_checks`, `secret`. |
| `waits.py` | `wait_until` puro (relógio injetável), polling 50 ms no 1º segundo e 100 ms depois, `HOST_LOST_AFTER` = 3 s offline. |
| `screen.py` / `keys.py` / `_codec.py` | Telas imutáveis, teclas/mnemônicos/chunks ≤ 128 bytes, cp1252 e posições. |
| `recipes/` | `Cics.logon` (MENU → SIGNON → senha em campo **oculto** conferido → `Signon`), `Cics.start_transaction`, `Cics.check` (erro grave), `Ims.logon` (fluxo da 0.1.5). |
| `compat.py` / `client.py` / `server.py` / `api.py` | API 0.1.5 sobre a `Session`; módulo legado; stub do servidor; stub com `ImportError`. |
| `_launcher.py` | Orquestra abrir/anexar/fechar (§6) com `User32` injetável, `get_backend`, relógio e `spawn`. |
| `_instances.py` | Classifica janelas (`launcher`, `ie`, `hllapi`, `launched`), diálogos conhecidos e `sanitize()`. |
| `_user32.py` | `RealUser32` (ctypes): `EnumWindows`, `InternalGetWindowText`, `PostMessageW(WM_CLOSE/BM_CLICK)`, `ShowWindowAsync` (nunca ativa). No sandbox, as ações levantam `SandboxViolation`. |
| `_legacy.py` | Acha servidores 0.1.5 nas portas 8500/8600/8700/8800 (só na própria sessão do Windows) e manda `disconnect`. |
| `_bridge.py` / `_backend.py` | Um `WorkerHandle` por letra no processo; `get_worker(letra, modo)` acha/sobe o worker, faz o handshake, recicla, traduz erros de transporte (§5). |
| `_runtime32.py` | Acha e valida o Python 32-bit (sonda `-I -S` de 10 s; ordem: argumento, `RUMBAPY_PYTHON32`, o próprio processo, `runtime.json`, `py -0p`, pastas padrão). |
| `_doctor.py` | Relatório: plataforma, RUMBA/launcher, Python 32-bit, update (alcance do `info/refs`, modo), workers vivos (`ping` admin, sem subir worker), servidores 0.1.5, logs. |
| `_paths.py` / `_locks.py` / `_log.py` | `HOME` = `%LOCALAPPDATA%\rumbapy` (`RUMBAPY_HOME`), nomes por `user_tag` (SID + sessão do Windows), travas por arquivo, logs rotativos. |

## 5. Worker 32-bit e canal

**Spawn** (`_bridge.spawn`): `<python32 real> -I -S -X faulthandler -c <BOOT> <pkgdir> --letter D --pipe <nome aleatório>
--keyfile authkey.bin --stop-event Local\rumbapy2-<tag>-D-stop --registry run\worker-<tag>-D.json --log ... --idle 1800
--expect-build <BUILD_ID> --commit <sha> [--dll ...] [--guard ...] [--fake-dll mod:fábrica --fake-path dir]`. O BOOT
cria um módulo `rumbapy` vazio apontando para a pasta do pacote, então `rumbapy/__init__.py` **não** roda no worker.
`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` (o worker sobrevive à IDE). A trava
`locks\spawn-<tag>-<L>.lock` garante um worker por letra.

**Boot** (`_worker/main.py`): confere 32-bit/≥ 3.12 (12), importa todos os módulos e confere o `build_id` (14), lê a
chave (13), reserva o pipe com `FILE_FLAG_FIRST_PIPE_INSTANCE` **antes** da DLL (11; DACL falhou → 18), checa colisão de
`LOWORD(pid)` com outros clientes HLLAPI (15), recusa DLL real no sandbox (17) e ao vivo sem guard (16), carrega a DLL
(10), grava o registro e passa a escutar.

**Threads**: T-accept (aceita conexões, máx. 8), T-conn por conexão (auth, handshake, validação contra `OPS`, `ping`
direto), **T-dll única** (fila FIFO de 64 jobs; toda chamada `WD_*`), T-watch (tick de 100 ms).

**Canal** (`_ipc.py`): pipe em modo mensagem, DACL `D:P(A;;GA;;;<SID>)(A;;GA;;;SY)`, `PIPE_REJECT_REMOTE_CLIENTS`; o
cliente confere `GetNamedPipeServerProcessId` contra o registro (senão `ChannelHijacked`). Autenticação mútua
HMAC-SHA256 com nonces dos dois lados sobre `authkey.bin` (32 bytes), prazo de 2 s por passo. Mensagens JSON (sem
pickle), no máximo 1 MiB. Handshake: `hello` (protocolo, versão, `build_id`, estado, dono, hwnd, guard) → `hello_ack`
(`owner`/`admin`, PID real do cliente, script) → `welcome`/`reject`. Ordem do cliente: protocolo → `stopping` → dono de
outro processo (`TerminalInUse`, nunca para o worker) → `build_id` diferente (worker velho sem dono: `shutdown` +
spawn; arquivos em disco mudaram: `UpdateRequired`).

**Lease e evict** (C.6): um dono por worker (o lease nasce no `hello_ack` `owner` e termina no EOF da conexão —
`release()`, fim do processo, Ctrl+C). Conexões `admin` (`ping`, `sessions`, `evict`, `shutdown`) não pegam lease.
`evict` (usado por `close_all`) desconecta a HLLAPI, marca a conexão do dono como despejada e avisa
`{"event":"evicted"}`: toda op posterior dela → `SessionReplaced`. `shutdown` e o evento de parada só valem sem dono.
Identidade da instância: o `connect` devolve o `hwnd` (`WD_GetSessionHWND`); a `Session` faz `bind_instance(hwnd)` e
toda op com `checks_hwnd` leva o hwnd — outro hwnd → `SessionReplaced`. Fim do lease com `build_id` novo em disco →
o worker sai limpo.

**Engine e delivery** (D.5): `connect` = `ConnectPS` + `SetSessionParamEx("NORESET")` + `QuerySessionStatus` +
`GetSessionHWND`; `write` valida campo protegido/tamanho **antes** de copiar (`ProtectedField`/`Truncated` com
`delivery="none"`); `send` manda chunks em ordem, a AID sozinha no último, e classifica a falha em `none`/`partial`/
`unknown`; depois do último chunk lê o OIA (`oia_after`). rc 1 numa op com o worker conectado → `reconnect_same()` e
repetição **uma vez** (só leitura ou entrada cuja 1ª chamada foi recusada): é o que absorve a queda da conexão HLLAPI
quando **outro** terminal é fechado (achado ao vivo L9).

**Máscara de campos ocultos** (`_worker/mask.py`, D.6): o worker enumera os campos (`FindFieldPosition/Length`,
`QueryFieldAttribute`) e troca por espaço os bytes de campos não exibidos e de spans escritos com `secret=True` antes de
qualquer resposta. Reenumera após `connect`, após resposta a uma AID e (no `snapshot`) quando o hash do PS muda sem
escrita nossa. Sem como calcular a máscara, a leitura é recusada (`HllapiError` "...para não expor conteúdo oculto") e
a tela vem com `hidden_unknown=True`. Limite (A.9 h): um span `secret` num campo **visível** cai quando o host responde à
AID seguinte — senha vai em campo oculto (as receitas conferem).

**Watchdog** (D.4, `_worker/watchdog.py`): limites por chamada — `WD_ConnectPS` 15 s, `WD_QuerySessions` 8 s,
`WD_DisconnectPS`/`WD_ResetSystem` no encerramento 5 s, demais 20 s; com suspensão do Windows (> 5 s entre ticks) os
prazos são rearmados. Estouro, janela do emulador sumida durante uma chamada (2 ticks) ou diálogo "HLLAPI ERROR" →
`fire`: agenda `os._exit(3)` em 2 s, cancela o job em curso (nenhum chunk depois), responde `WorkerHung`/
`SessionStopped`/`ClientTableFull`, grava `exit_reason` no registro e sai. Idle de 30 min sem dono → parada limpa
(`DisconnectPS` + `ResetSystem`) sob a trava de spawn.

**Recuperação no cliente** (C.7/C.8): EOF/pipe quebrado → `WorkerCrashed`; sem resposta no prazo → `WorkerHung`
(espera o PID sair até 30 s; nunca `TerminateProcess`). Op de leitura: recicla (`get_worker` → `connect` por até 15 s
tolerando rc 1 e rc 11) e repete 1×; op de entrada: levanta com `delivery="unknown"` e a `Session` fica `uncertain`.
Ctrl+C fecha a conexão (o worker cancela os jobs dela) e relança `KeyboardInterrupt`.

**Registro e logs** (D.7): `run\worker-<tag>-<L>.json` (pid, `created`, pipe, estado, dono, hwnd, `exit_reason`),
`logs\worker-<tag>-<L>.log` (rotativo 5 × 5 MB) e `logs\boot\...txt` (stderr do boot, últimos 5). Log por op: nome,
id, coordenadas, tamanhos (omitidos se `secret` ou se a tela pode ter campo oculto), rc, duração — nunca texto.

---

## 6. Fluxos

### 6.1 `rumbapy.open("D")` (`_launcher.Launcher.open`, E.5)

```
0. ensure_latest("open") (TTL); _legacy.neutralize("D") (1× por processo e letra); diálogo "HLLAPI ERROR" → ClientTableFull
1. admin = get_worker(D, "admin") (sobe o worker se preciso); antes = instâncias do perfil (E.3)
2. reuse=True e exatamente 1 instância → owner + connect por até 10 s → passos 7–9 (via="reuse")
3. close_all(perfil, admin=admin)  — fecha TODAS as instâncias (evict do dono de outro script incluído)
4. Popen("Host Access Web Launcher.exe", "hawlauncher://<portal>/profpro/BRAZIL_D.html/microfocus_w2h_hawlauncher")
   (sem launcher ou RUMBAPY_LAUNCH=ie: cscript _ie_open.js) ; grava o PID em run\launched-<tag>-D.json
5. registro: admin.sessions() a cada 250 ms (ao vivo: ~9–25 s); "Short Name" → MultipleInstances; minimiza cedo
6. owner = get_worker(D, "owner"); connect a cada 500 ms (rc 1 enquanto "Connecting To Address"; ao vivo ~11–27 s)
   → bind_instance(hwnd) logo depois do connect
7. primeira tela: snapshot pronto, online, não vazio e igual ao de ≥ 0,3 s antes (nada é digitado)
8. Session(backend, perfil, OpenInfo)  (o bind_instance já foi feito no passo 6)
9. window="minimized" → ShowWindowAsync(SW_SHOWMINNOACTIVE) (aplicado cedo, no registro); devolve a Session
10. qualquer falha depois do passo 4 → close_all restrito ao que este open lançou; release() do lease
```

`attach` (E.6) é não destrutivo: 2+ instâncias → `MultipleInstances`; nada registrado e nenhuma janela →
`TerminalNotOpen`; dono vivo em outro processo → `TerminalInUse`; connect até o prazo; a 1ª tela pode estar em branco
se estiver pronta e estável por 1 s (CICS depois de CLEAR). Nunca abre nem fecha janelas.

### 6.2 `rumbapy.close("D")` (`close_all`, E.4 com os ajustes A.14 g/l/m)

```
1. worker JÁ vivo da letra (nunca sobe um) → conexão admin → evict (o dono de qualquer processo vira "replaced")
2. instâncias = janelas principais do perfil (launcher/ie/hllapi/launched, sem as janelas auxiliares do processo)
3. janela oculta → SW_SHOWMINNOACTIVE ; 3b. modal "Short Name already in use" → OK antes do WM_CLOSE da dona
4. WM_CLOSE em cada janela principal (PostMessage; nunca TerminateProcess)
5. polling de 100 ms; aos 3 s e 8 s: diálogos conhecidos e novo WM_CLOSE
5b. espera a SAÍDA dos processos das instâncias, sem chamar a DLL (até 5 s; ao vivo: janela some em 0,11 s,
    processo sai em 0,33 s; close inteiro ~0,5 s)
6. só se a letra pode estar numa janela que não fechamos (ou o processo não saiu): sessions → InstanceNotClosable
7. janelas vivas no prazo → CloseFailed ; devolve o nº de janelas fechadas
```

`Session.close()` primeiro confere com o worker que ainda é dona da mesma instância (`status`); se não for
(`replaced`, ou lease de outro script), nada é fechado. `RumbaClient.close_terminal()` mantém a semântica da 0.1.5:
fecha todas as instâncias do perfil.

### 6.3 Tecla AID (`Session.press`, G.3)

`ready_stable` (teclado livre + 2 snapshots iguais) → `pre` → `send([AID])` → espera até o teclado travar
(`oia_after` ou depois), o texto ou o cursor mudarem, com os `error_checks` do perfil; prazo estourado →
`NoHostResponse` (`delivery="unknown"`, sessão `uncertain`). `send_keys("abc<Tab>1<Enter>")` = corpo sem AID
(chunks ≤ 128 bytes) + `press(AID)` por segmento. Nunca envia RESET sozinho (`NORESET` ligado).

### 6.4 Logon CICS (`Cics.logon`, B.8; âncoras validadas ao vivo no L6)

`snapshot(fields=True)` → MENU ("Select CICS Environment" na linha 14 e campo (22,56) desprotegido) → `fill(22,56,
"C")` + `enter` + `wait_text("TERMINAL",4,6)` → SIGNON (usuário (12,21), senha (13,21) desprotegida **e oculta**) →
`fill` usuário, `fill(senha, secret=True)`, `enter` → `wait_any` (`SENHA EXPIRADA`/`SENHA INVALIDA` em (19,19) antes de
`Signon` em (5,20)). Falha antes do ENTER apaga o campo de senha; entre a senha e a resposta, exceções saem sem `screen`.
Ao vivo: logon em 1,2 s; fechar a sessão logada não pede confirmação.

### 6.5 Multi-terminal

Cada letra tem seu worker e seu processo de launcher. Fechar um terminal derruba a conexão HLLAPI dos workers das
outras letras (causa interna do RUMBA não investigada); a próxima op deles dá rc 1 e é absorvida por
`reconnect_same()` (§5). Abrir não afeta os outros.

---

## 7. Auto-update obrigatório e heal (H)

* **Quando**: `import rumbapy` (papéis `user`/`cli`); `open`/`attach`/`RumbaClient()` com TTL de 1 h (interativo:
  Jupyter/REPL/IDLE/`python -i`) ou 12 h (script); `python -m rumbapy update` (força); `client.update_python_64bit()`.
* **Modo de instalação** (`_update.install_mode`): `RUMBAPY_DEV=1` → `dev` (avisado); distribuição instalada por
  `archive/<sha>.zip` → `archive`; `git+https` (0.1.x) → `vcs`; checkout/editable sem `RUMBAPY_DEV` → `UpdateFailed`
  (nada de modo dev implícito).
* **Commit remoto**: `info/refs?service=git-upload-pack` por HTTPS (5 s, 2 tentativas, proxy do sistema; Content-Type
  conferido), depois `git ls-remote`, depois a API do GitHub. Tudo falhou → `UpdateCheckFailed` (bloqueia).
* **Instalação**: sob `locks\update.lock` (marcador `update-in-progress.json` com o PID do pip), `pip install
  --force-reinstall --no-deps <archive/sha.zip>` desacoplado e sem prazo (o pip nunca é morto); confere com um
  interpretador novo que o commit instalado é o remoto; levanta `UpdateRequired` com a ação do contexto. Nunca
  reexecuta o script. Outro processo instalou outro commit durante a vida deste → `UpdateRequired("Outro processo
  atualizou o RumbaPy...")` sem rede.
* **Consistência**: `BUILD_ID` (sha256 dos `.py/.js/.json` do pacote) é calculado no import (antes e depois dos
  imports), antes de cada spawn, pelo worker no boot e ao fim de cada lease: nunca há cliente e worker de versões
  diferentes conversando como dono.
* **Heal** (`_bootstrap.heal`, congelado): qualquer erro não-`RumbaError` no `import` (arquivo ausente, sintaxe,
  import quebrado) → registra o traceback, reinstala o `master` atual (1 tentativa por commit por hora) e pede para
  rodar de novo. Um commit quebrado se corrige sozinho quando o `master` for corrigido.
* **Migração da 0.1.5** (H.8): o `RumbaClient` 0.1.5 em memória instala a v2 por `git+https` (backend PEP 517 in-tree);
  o antigo `python32 -m rumbapy.server` agora é o stub, que responde "rode de novo"; servidores 0.1.5 vivos são
  neutralizados (`disconnect`) pela v2.

---

## 8. Estado da `Session` (B.3)

| Estado | Entra quando | Efeito | Sai quando |
|---|---|---|---|
| `ok` | criada; espera de conteúdo bem-sucedida | normal | — |
| `uncertain` | exceção com `delivery` `partial`/`unknown`; `NoHostResponse`; `HostConnectionLost`; Ctrl+C durante entrada | entrada levanta `SessionUncertain` sem tocar a DLL; leituras e esperas funcionam | `wait_text/any/cursor/change` bem-sucedido ou `acknowledge()` |
| `replaced` | `SessionReplaced` do worker (evict, hwnd diferente) ou nova `open()` da letra no mesmo processo | toda op levanta `SessionReplaced` | nunca |
| `closed` | `close()` | toda op levanta `TerminalNotOpen` | nunca |

Quem libera o quê: `release()`/`with`/GC/`atexit` devolvem o lease (HLLAPI continua conectado no worker, janela
aberta); `disconnect()` também faz `WD_DisconnectPS`; `close()` revoga o lease de qualquer processo e fecha as janelas.

---

## 9. Testes e segurança operacional (J)

* `tests/conftest.py` liga o sandbox antes de qualquer import; o **código de produção** recusa no sandbox a DLL real,
  o `Popen` do launcher/`cscript`, `PostMessage`/`ShowWindow`, pip e rede sem os dublês (`SandboxViolation`).
* Camadas: puro (`tests/unit/core`, `contracts`), worker em processo (`tests/unit/worker`, `FakeEhllapi`), cliente
  (`tests/unit/client`, `InProcessBackend` + `FakeUser32`), ponta a ponta (`tests/e2e`, worker 32-bit real com
  `--fake-dll tests.fakes.scenarios:<fábrica>`), update (`tests/unit/update`, `tests/e2e/update`, GitHub e pip falsos),
  ao vivo (`tests/live` e `tools/research/live_*.py`, manuais, `RUMBAPY_LIVE=1` + `GuardedDll`).
* CI (`.github/workflows/ci.yml`): ruff + pyright (3.12, bloqueante), pytest 3.12/3.13/3.14 × x64/x86, install-smoke
  (zip do HEAD servido por um GitHub falso local → venv limpo → `pip install` → `import rumbapy` → BOOT do worker x86).

---

## 10. Limites conhecidos do desenho

* Fail-closed sem rede (sem válvula offline na v2.0, decisão D1/Q1 pendente).
* `UpdateRequired` custa uma execução extra por deploy (sem reexecução automática; Q2).
* `open`/`close` de um script interrompem o lote de outro na mesma letra (`SessionReplaced`) — pedido do dono (Q5).
* Bits finos do OIA (G1 SSCP-LU, bits individuais de G8, insert, erro de comunicação) não validados: só
  "G8 ≠ 0 = bloqueado" decide.
* Detalhes e pendências: [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) e `docs/ESPECIFICACAO_V2.md` §K.

# Plano de melhorias do RumbaPy

> Objetivo final: **`import rumbapy` e pronto** — controlar terminais RUMBA (CICS/IMS) da forma mais
> simples e robusta possível, **mantendo obrigatório o uso da última versão**.
> Base: auditoria do código (`docs/KNOWN_ISSUES.md`) e engenharia reversa do RUMBA
> (`docs/RUMBA_EHLLAPI.md`, `docs/RUMBA_AMBIENTE.md`). Escrito em 2026-10-03.

---

## 0. Resumo executivo

1. **O núcleo técnico (EHLLAPI via `ehlapi32.Dll`) está certo, mas o entorno é frágil.** Os problemas
   reais são: (a) o servidor 32-bit antigo continua rodando depois de um update (o requisito de
   "última versão" **não** é garantido hoje); (b) encoding errado (acentos derrubam leituras);
   (c) chamadas que travam para sempre; (d) abertura do terminal dependente do IE; (e) nenhum log;
   (f) socket sem autenticação.
2. **Proposta central:** um **worker 32-bit só com stdlib**, executado *a partir do pacote 64-bit*
   (sem instalar nada no Python 32-bit). Isso **elimina a atualização dupla** e torna impossível
   rodar versões diferentes nos dois lados. Um worker por sessão (letra), uma thread de DLL,
   *named pipe* autenticado, *watchdog* que recicla o worker se a DLL travar, *handshake* de versão.
3. **Abrir o terminal sem automatizar o IE**, via o **Host Access Web Launcher** (`hawlauncher://`,
   já instalado), com *fallback* para o caminho IE atual; e **reaproveitar** sessões já abertas.
   Janela controlada pelo HWND que a própria DLL informa (`WD_GetSessionHWND`, `WD_ShowSession`).
4. **API de alto nível** (`Session`, `Screen`, `Field`, `Key`, esperas por OIA/texto, exceções
   tipadas, receitas de logon CICS/IMS) **com camada de compatibilidade** que mantém o `RumbaClient`
   atual funcionando sem mudar scripts existentes.
5. **Auto-update 2.0, obrigatório:** checagem única, antes de carregar o resto do pacote, sem exigir
   Git, com reexecução automática do script atualizado e verificação de versão do worker.
6. **"Forma ainda mais eficiente" (opcional, depende de aprovação):** um *backend* **TN3270 direto**
   (Python 64-bit puro, sem RUMBA/IE/32-bit, *headless*, várias sessões em paralelo), atrás da mesma
   API. Os perfis mostram que as sessões são TN3270 simples (host + porta telnet padrão, LU de
   pool; detalhes em `docs/interno/`). Precisa de aval de Segurança/Mainframe antes de existir.

---

## 1. Princípios

| Princípio | Consequência prática |
|---|---|
| **Zero configuração** | Descobrir Python 32-bit, DLL, perfis e portas sozinho; nada de `config.ini`. |
| **Sempre a última versão** (obrigatório) | Update antes de qualquer outra coisa; versão do worker = versão do cliente, sempre. |
| **Falhar alto e cedo** | Todo rc da DLL vira exceção tipada com mensagem em português e o rc original. |
| **Nunca travar** | Toda chamada tem prazo; worker travado é reciclado; nada de `INFINITE`. |
| **Não mexer no que não é nosso** | Fechar só as janelas/sessões que abrimos; nunca por regex de título. |
| **Observável** | Log rotativo por worker; nada de senha em log. |
| **Compatível** | `RumbaClient` e seus métodos continuam existindo (implementados sobre a nova base). |
| **Testável sem mainframe** | Lógica de alto nível testada com um *backend* falso. |

---

## 2. Decisões de arquitetura (ADRs resumidos)

### ADR-01 — Manter EHLLAPI (`ehlapi32.Dll`) como *backend* padrão
* **Por quê:** é a interface oficial do produto instalado, já validada em produção pelo projeto;
  cobre tudo que precisamos (PS, campos, OIA, cursor, busca, notificações, janela da sessão).
* **Alternativas:** WinHLLAPI (`WHLLAPI.dll`) — mesma família, esperas finitas e cancelamento, mas
  exige `WinHLLAPIStartup` e é menos testada aqui → manter como plano B. DDE (`RUMBADSP`) → permite
  64-bit mas é mais pobre e legado → plano C. Reimplementar o protocolo interno (`HLLAPIMem` +
  `"WDHLLAPI SEND"`) sem a DLL → **rejeitado** (não documentado, depende da seção `.SHARDAT`).

### ADR-02 — Worker 32-bit "stdlib-only" embarcado no pacote 64-bit
* O código que roda no 32-bit fica em `rumbapy/_worker/` e usa **apenas** `ctypes`, `json`,
  `threading`, `logging`, `multiprocessing.connection`. Ele é executado como
  `python32.exe -E -s <site-packages>/rumbapy/_worker/__main__.py ...`.
* **Consequências:** não é preciso `pip install` nada no Python 32-bit (adeus `pywin32`/`pywinauto`
  e a 2ª instalação do rumbapy); o worker é **sempre** a mesma versão do cliente; o auto-update só
  atualiza um ambiente. O Python 32-bit pode até ser o *embeddable zip* oficial (ver ADR-09).
* `pywin32`/`pywinauto` são substituídos por chamadas Win32 via `ctypes` (EnumWindows, PostMessage
  `WM_CLOSE`, ShellExecute) — já provado nos scripts de `tools/research/`.

### ADR-03 — Um worker por sessão (letra), uma única thread de DLL
* A identidade HLLAPI é **por processo** (ver `RUMBA_EHLLAPI.md` §4). Um processo por letra dá
  isolamento natural: travou o `D`, o `Z` continua; reciclar o worker não afeta outras sessões.
* Dentro do worker, **todas** as chamadas à DLL acontecem numa thread dedicada (fila); outra thread
  atende o *pipe* e o *watchdog*. Nunca criar threads por chamada (evita o esgotamento dos 32 slots
  caso o modo multithread seja ligado e o vazamento de threads zumbis).
* `MultiThreadConnectionOn()` fica **desligado** (não precisamos dele com um processo por sessão).

### ADR-04 — Transporte: *named pipe* autenticado e persistente
* `multiprocessing.connection` (`AF_PIPE`, `authkey` HMAC) em `\\.\pipe\rumbapy-<usuário>-<letra>`;
  chave aleatória em `%LOCALAPPDATA%\rumbapy\` (perfil do usuário). Mensagens JSON
  (`send_bytes`/`recv_bytes`) — sem `pickle`, para não depender de versões iguais de Python.
* Conexão **persistente** (fim do socket por comando e do `WinError 10048`), protocolo versionado:
  `{"v": 1, "id": n, "op": "...", "args": {...}, "deadline_ms": ...}` → `{"id": n, "ok": true, "result": ...}`
  ou `{"id": n, "ok": false, "error": {"type": "KeyboardLocked", "rc": 5, "msg": "..."}}`.
* *Handshake* na conexão: worker informa `version`, `commit`, `pid`, `python`, `dll_version`; o
  cliente recicla o worker se divergir (**fecha o furo 1.1 do KNOWN_ISSUES**).

### ADR-05 — Abertura do terminal em cascata
1. **Anexar**: se `WD_QuerySessions` já mostra a letra (sessão aberta por qualquer meio), conectar.
2. **Host Access Web Launcher**: `Popen(["…\Host Access Web Launcher.exe",
   "hawlauncher://https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/<PERFIL>.html/microfocus_w2h_hawlauncher"])`
   (formato confirmado no IL: exatamente 1 argumento com o sufixo) e aguardar a letra aparecer em
   `WD_QuerySessions` (com prazo e diagnóstico). Vantagens: **um processo por sessão, cujo PID é nosso**;
   sem COM do IE; o `WindowState` por URL fica em `%LOCALAPPDATA%\Micro Focus\Host Access Web Launcher\
   Host Access Web Launcher.config`, então dá para abrir já minimizado.
3. **IE COM (legado)**: o caminho atual (`InternetExplorerMedium`), mantido como *fallback*
   enquanto o IE COM existir no Windows.
* Depois de aberta: `WD_GetSessionHWND` → HWND/PID da sessão (sem varrer `Shell.Application`);
  `WD_ShowSession` para minimizar/ocultar/mostrar; fechar com `WM_CLOSE` **nessa** janela.
* Mapeamento letra → perfil num único lugar (`D→BRAZIL_D`, `Z→BRAZIL_Z`, `A→IMS_A`, extensível),
  usando `https://`.
* Opção futura (ADR-05b): hospedar `NetManage.ProControlContainer.1` (**não licenciado**) no próprio
  worker (ATL `AtlAxWin`), replicando o que o `procontainerfunc.js` faz (`CreateSession(1)`, Telnet,
  HLLAPI, `Connect()`) — controle total e janela oculta. Alternativa equivalente: `RumbaRDEFactory`
  (.NET) em modo `StandAlone`. Ambas exigem validar a licença do display (`WdMfDsp` é licenciado; no
  portal ela chega pelas chaves `kc1..kc4` do perfil).

### ADR-06 — Prazo em tudo + *watchdog* com reciclagem
* Cada operação tem prazo (padrão por tipo; configurável). Se a DLL não voltar no prazo, o worker
  **sai com `os._exit()`** (ExitProcess → o `DllMain` da DLL limpa a tabela compartilhada) e o cliente
  sobe outro e reconecta à sessão (que continua aberta no emulador). `TerminateProcess` só como último
  recurso (não roda o *cleanup* e vaza entradas da tabela de 32 clientes).
* Erros transitórios conhecidos (rc 4 "busy", rc 5 "inhibited") têm política explícita: esperar
  OIA/`WD_Wait`, `RESET` opcional, nova tentativa limitada.

### ADR-07 — Auto-update 2.0 (obrigatório, reforçado)
Ver §5.

### ADR-08 — Texto e coordenadas corretos
* Decodificar com o code page do Windows (`cp1252`, a validar ao vivo; configurável), `errors="replace"`;
  `NUL` → espaço; nunca `utf-8`/`ascii`.
* Descobrir linhas×colunas com `WD_QuerySessionStatus` (IMS A é 32×80) em vez de assumir 24×80/80 colunas.
* Escapar `@` como `@@` ao digitar texto livre via `SendKey`.

### ADR-09 — Provisionamento do Python 32-bit (opcional, recomendado)
Ordem de busca: argumento → `RUMBAPY_PYTHON32` → `py -0p` (entradas `-32`) → pastas padrão →
runtime privado em `%LOCALAPPDATA%\rumbapy\python32\`. Se nada existir, **baixar o embeddable zip
oficial** (`python-3.13.x-embed-win32.zip`, SHA-256 fixado no código) e extrair ali. Com o ADR-02,
o usuário final só precisa instalar o Python 64-bit e `pip install` o rumbapy.

### ADR-10 — *Backends* plugáveis
Interface interna `Backend` (connect, read_screen, write, send_keys, wait, query_status...) com:
`EhllapiBackend` (padrão), `FakeBackend` (testes), e futuramente `Tn3270Backend` (Fase 6) e
`DdeBackend` (se necessário). A API pública não muda entre eles.

---

## 3. Arquitetura-alvo

```
Python 64-bit (script do usuário)
└─ rumbapy
   ├─ __init__.py ......... 1º: _update.ensure_latest()  2º: exporta API pública
   ├─ _update.py .......... auto-update obrigatório (sem git; reexec; política offline)
   ├─ _runtime32.py ....... encontra/provisiona o Python 32-bit
   ├─ _bridge.py .......... sobe/conecta/recicla workers; handshake; prazos; reconexão
   ├─ session.py .......... Session: open/attach/close, type/press/read/wait/fill, contexto `with`
   ├─ screen.py ........... Screen/Field/Cursor/OIA (parsing puro, 100% testável)
   ├─ keys.py ............. Key (enum completo, extraído do WDApiSvc.ocx)
   ├─ errors.py ........... hierarquia de exceções
   ├─ recipes/cics.py ..... logon CICS, detecção "E R R O   G R A V E", senha expirada/inválida
   ├─ recipes/ims.py ...... logon IMS/RHELP
   ├─ compat.py ........... RumbaClient (API antiga) sobre Session
   ├─ testing.py .......... FakeBackend + telas gravadas para testes de scripts
   └─ _worker/ ............ (executado pelo Python 32-bit, só stdlib)
      ├─ __main__.py ...... loop do pipe, thread da DLL, watchdog, logs
      ├─ hllapi.py ........ bindings ctypes de TODAS as WD_* + structs + rc→exceção
      └─ launcher.py ...... attach / hawlauncher / IE; HWND; mostrar/ocultar/fechar
            │  named pipe autenticado (JSON)
            ▼
      ehlapi32.Dll ──(HLLAPIMem + "WDHLLAPI SEND")──► WDApiSvc.ocx no processo do emulador ──TN3270──► mainframe
```

---

## 4. API pública proposta

```python
import rumbapy
from rumbapy import Key

with rumbapy.open("D") as t:               # anexa ou abre BRAZIL D (hawlauncher → IE), conecta
    t.cics.logon(usuario, senha)           # levanta PasswordExpired / InvalidPassword / HostError
    t.type("SPRE", row=1, col=2).press(Key.ENTER)
    t.wait_text("VOLVO", row=1, col=2, timeout=30)
    t.fill(3, 14, pn).press(Key.ENTER)     # escreve no campo (cp1252, '@' escapado)
    msg = t.read(23, 7, 74).strip()        # leitura correta com acentos
    print(t.screen.text)                   # tela inteira (24×80 ou 32×80)
    for f in t.screen.fields: ...          # campos: posição, tamanho, protegido, numérico, texto
    t.press(Key.PF10)
# saída do `with`: desconecta; fecha o terminal só se foi aberto por nós (configurável: keep_open=True)

# API antiga continua funcionando (compat):
from rumbapy import RumbaClient
cics = RumbaClient("D")
cics.logon_cics("A000000", "senha")
cics.copy_string_to_ps(1, 2, "SCOM"); cics.send_key("ENTER")
```

Exceções (todas herdam de `RumbaError` e carregam `rc`, `function`, `session`):
`NotConnected`, `SessionNotFound`, `SessionInUse` (rc 11), `HostBusy` (rc 4), `KeyboardLocked` (rc 5),
`InvalidPosition` (rc 7), `Truncated` (rc 6), `TextNotFound` (rc 24), `WaitTimeout`,
`WorkerCrashed`, `LaunchFailed`, `UpdateRequired`, `PasswordExpired`, `InvalidPassword`, `HostError`.

---

## 5. Auto-update 2.0 (requisito obrigatório)

| Hoje | Proposto |
|---|---|
| `git ls-remote` 2× por `RumbaClient` (exige Git) | 1 consulta por processo, via HTTPS puro (`/info/refs?service=git-upload-pack` do GitHub, o mesmo que o `git ls-remote` usa) → **sem Git** |
| `pip install git+https://...` (exige Git) | `pip install https://github.com/CamargoEduardo/RumbaPy/archive/<sha>.zip` → **sem Git**; commit gravado num arquivo de *build info* |
| Atualiza 2 ambientes (32 e 64) | Atualiza **só o 64-bit** (worker é embarcado — ADR-02) |
| Processo atual segue com código velho | Checagem **em `rumbapy/__init__.py`, antes de importar o resto**; se atualizou: script → reexecuta a si mesmo (`subprocess.run([sys.executable, *sys.argv])` + `sys.exit(rc)`); REPL/Jupyter → `UpdateRequired("reinicie o kernel")` |
| Servidor antigo continua vivo | *Handshake* de versão: worker de outra versão é encerrado e substituído |
| Falha de rede → `CalledProcessError` críptico | Mensagem clara; política **fail-closed** por padrão (requisito), com válvula de emergência documentada (ver decisão D1) |
| `RumbaClient(auto_update=False)` desliga a obrigatoriedade para qualquer usuário | Parâmetro aceito por compatibilidade, mas só tem efeito com `RUMBAPY_DEV=1` (modo desenvolvimento, registrado em log) |

Fluxo:
```
import rumbapy
 └─ _update.ensure_latest()
      ├─ já checado neste processo? → segue
      ├─ remote = HEAD do GitHub (timeout curto)
      ├─ local  = commit do build instalado
      ├─ iguais → segue
      └─ diferentes → pip install <zip do sha> → reexecuta script / pede reinício
```

---

## 6. Roteiro em fases

Cada fase é entregável sozinha, com PRs pequenos e sem quebrar scripts existentes.

### Fase 0 — Correções rápidas e rede de segurança (≈1–2 dias)
Sem mudar arquitetura; ataca os itens críticos de `KNOWN_ISSUES.md`.
- [ ] `ping` devolve `version`/`commit`; cliente encerra servidor divergente (1.1).
- [ ] `cp1252` + `errors="replace"` na leitura/escrita; `buffer.raw` + `NUL→espaço` (2.1–2.3).
- [ ] Corrigir/remover `wd_query_session_status` (2.4).
- [ ] Log rotativo do servidor em `%LOCALAPPDATA%\rumbapy\logs`; não logar senhas (6.1, 5.2).
- [ ] `https://` no `base_url`; erro imediato se o link do perfil não existir (4.3, 4.4).
- [ ] Laço de conexão no `logon_rhelp`; logar (sem quebrar) rc ≠ 0 de `SendKey`/`Wait` (4.5, 4.6).
- [ ] `.gitignore` para `*.egg-info`; validar `terminal_type` no cliente (7.1, 6.3).
- **Aceite:** scripts atuais rodam iguais; tela com acento lida corretamente; update troca o servidor.

### Fase 1 — Núcleo HLLAPI tipado (`_worker/hllapi.py`)
- [ ] Bindings `argtypes/restype` de **todas** as `WD_*` (tabela em `RUMBA_EHLLAPI.md` §3), criados uma vez.
- [ ] Estruturas: `SessionStatus`, `SessionInfo` (QuerySessions), `OIA`, `FieldAttribute`, `SystemInfo`.
- [ ] Mapa rc → exceção por função; mensagens PT-BR.
- [ ] `Key` com a tabela completa do emulador; escape de `@`; `send_keys` que quebra texto em pedaços de
      ≤ 128 bytes e garante **uma tecla AID por chamada** (o emulador descarta o que vem depois da 1ª AID).
- [ ] Testes unitários com uma DLL falsa (objeto Python com a mesma interface).
- **Aceite:** 100% das funções usadas hoje cobertas; testes rodando em qualquer máquina.

### Fase 2 — Worker e ponte novos
- [ ] `_worker/__main__.py` stdlib-only; thread da DLL + fila; *watchdog*; `os._exit` em travamento.
- [ ] `_bridge.py`: descoberta do runtime 32-bit, *spawn*, pipe autenticado, *handshake*, prazos, reconexão.
- [ ] Encerramento: *idle timeout*, comando `shutdown`, limpeza (`WD_DisconnectPS` + **`WD_ResetSystem`**,
      que o manual WD_API manda chamar sempre antes de sair).
- [ ] Remover dependência de `pywin32`/`pywinauto` e do 2º `pip install`.
- **Aceite:** matar/travar o worker no meio de um laço → cliente recupera sozinho e o script continua.

### Fase 3 — Launcher e ciclo de vida do terminal
- [ ] `attach` → `hawlauncher://` → IE COM (cascata), com diagnóstico do que falhou.
- [ ] HWND via `WD_GetSessionHWND`; `show/hide/minimize` via `WD_ShowSession`; `close` por `WM_CLOSE` no HWND próprio.
- [ ] Mapa letra → perfil configurável; suporte às outras letras do portal (ex.: `IMS_F`, `IMS_S`).
- **Aceite:** abrir/fechar `D`, `Z` e `A` sem IE COM; sessão manual do usuário nunca é fechada.

### Fase 4 — API de alto nível + compatibilidade
- [ ] Estratégia de espera alinhada ao fabricante: `WD_Wait`/OIA dizem só se o teclado aceita digitação;
      "tela pronta" = texto-chave esperado e/ou cursor no campo certo (manual WD_API).
- [ ] `Session`, `Screen`, `Field`, esperas (`wait_ready` por OIA/`WD_Wait`, `wait_text` com `WD_SearchPS`
      ou *snapshot*, `wait_change`, `wait_settle` = "host quieto por N ms", como o *settle time* dos
      `RDE_SendKeys_Hsynch` e o `WaitHostQuiet` do RumbaScriptAT), *polling* de 50–100 ms, prazos configuráveis.
- [ ] Apelidos no estilo EXTRA!/RumbaScriptAT para quem já conhece (`get_string`, `put_string`,
      `wait_for_string`, `wait_host_quiet`).
- [ ] `recipes/cics.py`, `recipes/ims.py` (fluxos atuais de `server.py`, com exceções tipadas e *hooks* de erro).
- [ ] `compat.RumbaClient` com os mesmos métodos/assinaturas de hoje.
- [ ] README novo (PT-BR), exemplos, *docstrings*.
- **Aceite:** `run.py` original (SPRE) e um script RHELP rodam com a API nova e com a compat.

### Fase 5 — Auto-update 2.0 e instalação "um comando"
- [ ] `_update.py` conforme §5; testes simulando HEAD remoto diferente/offline.
- [ ] `_runtime32.py` com provisionamento do embeddable (ADR-09), se aprovado (decisão D3).
- [ ] README de instalação: Python 64-bit + `pip install https://github.com/.../archive/HEAD.zip`.
- **Aceite:** máquina "limpa" (sem Git, sem Python 32-bit) → `pip install` + `import rumbapy` funciona.

### Fase 6 — *Backend* TN3270 direto (PoC, condicionado à decisão D2)
- [ ] PoC com **`tnz`** (IBM, Apache-2.0, Python puro ≥ 3.10, v0.6.7 de 2026-09, ativo; TN3270E opcional
      via `SESSION_TN_ENHANCED=1`; atenção: o padrão é **TLS na porta 992** — porta 23 = texto puro) contra o
      host CICS-BR (`docs/interno/`) em homologação, se houver. Alternativas: `pytn3270` (ISC, puro),
      `py3270` (BSD, embrulha `ws3270`, parado desde 2018).
- [ ] Code pages: `HostCharacterSet=56` = `CHARSET_SWEDISH_EURO` → **cp1143** (BRAZIL D/Z) e `0` → **cp037**
      (IMS A). O cp1143 não está na stdlib, mas está no pacote **`ebcdic`** (BSD), que o `tnz` importa sozinho.
- [ ] Validar: aceitação do *terminal type* (`IBM-3278-2-E` / `-3-E`), LU de pool, `TypeAhead`/`AutoReset`.
- [ ] `Tn3270Backend` atrás da mesma API: `rumbapy.open("D", backend="tn3270")`.
- **Ganhos:** sem RUMBA/IE/32-bit/GUI, roda em servidor/agendador, sessões paralelas, latência de
  milissegundos, estado exato do teclado (sem *polling* de janela).
- **Riscos:** política de segurança/licenciamento, auditoria do mainframe, diferenças de comportamento.

### Fase 7 — Qualidade contínua
- [ ] `ruff` + `pyright`/`mypy`; `pytest` com `FakeBackend`; GitHub Actions (Windows) para testes unitários.
- [ ] Tags/versões semânticas + `CHANGELOG.md`; o auto-update continua seguindo HEAD (ou a última tag, se decidido).

---

## 7. Riscos e mitigação

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Microsoft remover a automação COM do IE (já **sem suporte** no Windows 11; o compromisso "até 2029" cobre só o modo IE do Edge) | Média-alta | Alto (não abre terminal) | Fase 3 (`hawlauncher`), ADR-05b, Fase 6 |
| Portal mudar perfis/URLs | Média | Médio | Mapa letra→perfil configurável; erro imediato e claro |
| Emulador travar no meio de uma chamada | Média | Alto | ADR-06 (prazo + reciclagem) |
| Popup "Exceeded maximum number of clients" | Baixa | Alto (bloqueia) | Processo por sessão, sem threads por chamada, saída limpa (`os._exit`), nunca `TerminateProcess` |
| Atualização quebrar todos os usuários ao mesmo tempo | Média | Alto | Testes automatizados (Fase 7), PRs pequenos, possibilidade de seguir tags |
| GitHub inacessível | Baixa | Médio | Política offline explícita (D1) |
| Code page errado | Média | Médio | Validação ao vivo (§8) e parâmetro `encoding=` |

---

## 8. Validações pendentes (precisam de uma sessão real)

1. Code page devolvido pela DLL em telas com acento (`cp1252` vs `cp850`) — ler bytes crus de uma tela conhecida.
2. `hawlauncher://` abrindo `BRAZIL_D` e a letra `D` aparecendo em `WD_QuerySessions`; qual processo hospeda a sessão.
3. `WD_GetSessionHWND` e `WD_ShowSession(SW_HIDE/SW_MINIMIZE)` na sessão aberta.
4. Tópicos/itens DDE do `RUMBADSP` (`tools/research/dde_list.py`).
5. Mapeamento `HostCharacterSet=56` → code page EBCDIC (necessário só para a Fase 6).
6. Tempo real de `WD_Wait` com teclado travado (esperado: TWAIT ≈ 60 s).

## 9. Decisões que dependem de você

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| D1 | Comportamento do auto-update **sem acesso ao GitHub** | (a) bloquear sempre; (b) bloquear, com válvula `RUMBAPY_ALLOW_OFFLINE=1` registrada em log; (c) avisar e seguir | **(b)** — mantém a obrigatoriedade sem parar a operação numa queda do GitHub |
| D2 | Investigar o *backend* **TN3270 direto** | levar à Segurança/Mainframe ou não | Levar; é a forma mais eficiente e robusta, mas só com aval |
| D3 | Baixar automaticamente o Python 32-bit *embeddable* | sim / não (exigir instalação manual) | Sim, se a política de TI permitir downloads de python.org |
| D4 | Seguir HEAD ou *tags* no auto-update | HEAD (hoje) / última tag | HEAD no curto prazo; tags quando houver CI |
| D5 | Ao sair do `with`, fechar o terminal? | fechar se aberto por nós / manter aberto | Manter aberto por padrão (reaproveita login entre execuções), `close=True` opcional |
| D6 | Pedir à TI o **Rumba+ Desktop 10.2 SP1+** (traz `EHLAPI64.DLL`, API oficial 64-bit) | sim / não | Só se a Fase 6 for vetada e o Python 32-bit incomodar; o ADR-02 já esconde o 32-bit do usuário |
| D7 | O que publicar no GitHub (repositório **público**) | tudo / sem a engenharia reversa / repositório privado | Dados internos já ficam em `docs/interno/` (ignorado). A descrição detalhada da DLL (`docs/RUMBA_EHLLAPI.md`) e `tools/research/` podem conflitar com a licença (EULA) do RUMBA e expõem o ambiente: **avaliar com o jurídico/Segurança antes do push**, ou tornar o repositório privado |

# Problemas conhecidos (v2.0.0, branch `dev`, 2026-10-04)

Três partes: **(A)** o status de cada problema da auditoria da 0.1.5 (commit `5c03d47`; as referências `arquivo:linha`
antigas valem para aquele commit), **(B)** problemas e limites atuais da v2 (inclusive os achados ao vivo de
2026-10-03/04) e **(C)** pendências que dependem do dono do projeto antes do merge no `master`.

Severidade: **C** = crítico, **A** = alto, **M** = médio, **B** = baixo. Status: ✅ resolvido · 🟡 mitigado (resta
algo) · ⏳ aberto · ➖ não se aplica mais.

## A. Auditoria da 0.1.5 — status na v2

### A.1 Requisito "sempre a última versão"

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 1.1 | **C** | Servidor 32-bit antigo reutilizado sem checar versão (rodava código velho após o update) | ✅ cliente e worker conferem o `build_id` no handshake; worker velho sem dono é parado e trocado; o worker confere o próprio build no boot (saída 14) e ao fim de cada lease | `_bridge.handshake`, `_worker/main.py`, `_worker/server.py` |
| 1.2 | **A** | Update no interpretador em execução, que seguia com os módulos antigos | ✅ checagem antes de importar o resto do pacote; depois de instalar, sempre `UpdateRequired` ("rode de novo"); `BUILD_ID` recalculado no fim do import | `__init__.py`, `_update.ensure_latest` |
| 1.3 | M | `git ls-remote` 2× por `RumbaClient()`, exige Git, erro cru sem rede | ✅ uma checagem por processo (+ TTL 1 h/12 h no `open`), cache de 60 s, HTTPS sem Git (Git e API do GitHub como reserva), `UpdateCheckFailed` em português | `_bootstrap.remote_master_sha`, `_update` |
| 1.4 | M | `auto_update=False` desligava a obrigatoriedade | ✅ ignorado (WARNING) salvo com `RUMBAPY_DEV=1`, que é logado e avisado a cada import | `compat.RumbaClient.__init__`, `_update.install_mode` |

### A.2 Corrupção de dados / encoding

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 2.1 | **C** | `.decode("utf-8")` em texto cp1252 | ✅ cp1252 (validado ao vivo, inclusive `ç ã É €`) | `_codec.py` |
| 2.2 | **A** | `.encode("ascii")` na escrita | ✅ cp1252 estrito com `EncodingError` claro (`errors="replace"` opcional); NFC | `_codec.encode`, `Session.write/fill` |
| 2.3 | **A** | `buffer.value` cortava no 1º NUL | ✅ `.raw[:n]`, NUL → espaço; leituras com o tamanho pedido (a compat mantém o corte da 0.1.5 em `copy_ps_to_string`) | `_codec`, `_worker/engine.read` |
| 2.4 | **C** (latente) | `WD_QuerySessionStatus` com buffer imutável de 1 byte | ✅ buffer de 20 B com a letra no byte 0; layout 32-bit validado ao vivo | `_worker/hllapi.py`, `_worker/structs.py` |
| 2.5 | B | `copy_field` limitado a 80 bytes | ✅ tamanho real por `WD_FindFieldLength` | `Session.read_field`, `compat.copy_field` |

### A.3 Travamentos e concorrência

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 3.1 | **C** | `@timeout` abandonava threads presas na DLL | ✅ watchdog encerra o worker com `os._exit(3)` (nunca `TerminateProcess`); leituras repetidas 1× num worker novo | `_worker/watchdog.py`, `_bridge` |
| 3.2 | **A** | Thread por conexão TCP; `disconnect` de um cliente derrubava os outros | ✅ um worker por letra, uma única thread de DLL, um dono (lease) por vez | `_worker/server.py` |
| 3.3 | M | Thread por chamada (risco com `MultiThreadConnectionOn`) | ✅ T-dll de vida longa; `MultiThreadConnectionOn` nunca é usado | `_worker/server.py` |
| 3.4 | M | `screen_load` com polling de 1 s e 2 min fixos | ✅ polling 50 ms/100 ms, prazos configuráveis | `waits.py` |
| 3.5 | M | Dicionário `sessions` nunca limpo, sem lock | ➖ não existe mais; ciclo de vida explícito (lease + `release`/`disconnect`/`close`) | `session.py`, `_bridge.py` |

### A.4 Ciclo de vida do terminal

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 4.1 | **A** | Fechamento por regex de título podia fechar qualquer janela | ✅ só janelas que são sessões RUMBA do perfil (imagem + classe + título, ou HWND do HLLAPI); `WM_CLOSE` só na janela principal; diálogo "Short Name" tratado | `_instances.classify`, `_launcher.close_all` |
| 4.2 | **A** | `logon_*` sempre fechava o terminal existente | ✅ `attach` e `open(reuse=True)` não fecham; `open()` fecha todas (requisito do dono); a compat `logon_*` mantém a semântica da 0.1.5 (abre de novo) de propósito | `session.py`, `compat.py` |
| 4.3 | **A** | Dependência do `InternetExplorerMedium`; erro enganoso 60 s depois | ✅ Host Access Web Launcher; IE só de reserva (`_ie_open.js`), com `LaunchFailed` imediato e o passo que falhou | `_launcher._launch` |
| 4.4 | M | Portal em `http://` | ✅ `https://` (`profiles.PORTAL_DEFAULT`) | `profiles.py` |
| 4.5 | M | `logon_rhelp` sem checar o rc do `ConnectPS` | ✅ laço de `connect` com diagnóstico para todo perfil | `_launcher` (E.5 passo 6) |
| 4.6 | M | rc de `SendKey`/`Wait` ignorados | ✅ todo rc vira exceção tipada com `delivery` | `_worker/rc.py`, `_worker/engine.py` |
| 4.7 | B | "Erro grave" testado também no IMS | ✅ `error_checks` por perfil (só CICS) | `session.py`, `waits.cics_error_check` |
| 4.8 | B | Sem `WD_ResetSystem` ao encerrar | ✅ parada limpa faz `DisconnectPS` + `ResetSystem` | `_worker/server.py` |

### A.5 Segurança

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 5.1 | **A** | Socket TCP sem autenticação, acessível a outros usuários | ✅ named pipe com DACL do usuário, `PIPE_REJECT_REMOTE_CLIENTS`, HMAC mútuo, conferência do PID do servidor | `_ipc.py` |
| 5.2 | M | Requisição bruta (com senha) no log de erro | ✅ texto lido/escrito nunca vai a log; conteúdo de campo oculto nunca sai do worker | `_worker/mask.py`, `_worker/dispatch.py`, `_log.py` |
| 5.3 | B | Senha em JSON claro no localhost | ➖ só pelo pipe autenticado do próprio usuário | `_ipc.py` |

### A.6 Operação / observabilidade

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 6.1 | **A** | Zero diagnóstico do lado 32-bit | ✅ logs rotativos do cliente, de cada worker, do boot, do update e do pip; `python -m rumbapy doctor` | `_log.py`, `_doctor.py` |
| 6.2 | M | Servidor nunca terminava | ✅ idle de 30 min sem dono, evento de parada, `shutdown` | `_worker/watchdog.py` |
| 6.3 | M | Letra inválida só falhava 30 s depois | ✅ `UnknownTerminal` na hora | `profiles.resolve` |
| 6.4 | B | `except:` nu | ✅ código reescrito sem `except` nu | — |
| 6.5 | B | Um socket por comando (`TIME_WAIT`, `WinError 10048`) | ✅ conexão persistente por `WorkerHandle` | `_bridge.py` |

### A.7 Empacotamento / repositório

| # | Sev | Problema (0.1.5) | Status na v2 | Onde |
|---|---|---|---|---|
| 7.1 | B | `src/rumbapy.egg-info/` versionado | ✅ removido do Git | — |
| 7.2 | B | `requires-python >=3.12` × 32-bit ≥ 3.13 | ✅ ≥ 3.12 nos dois lados | `pyproject.toml`, `_runtime32.py` |
| 7.3 | B | README desatualizado | ✅ reescrito para a v2 | `README.md` |
| 7.4 | B | Sem testes, lint ou type-check | ✅ pytest (unit/e2e/update), ruff, pyright, CI 3.12–3.14 × x64/x86, install-smoke | `tests/`, `.github/workflows/ci.yml` |

## B. Problemas e limites atuais da v2

### B.1 Achados ao vivo (2026-10-03/04, roteiros L1–L9 e compat)

| # | Sev | Achado | Status | Onde |
|---|---|---|---|---|
| N1 | **A** | **Fechar um terminal derruba a conexão HLLAPI dos outros** (a janela do outro fica intacta; a próxima chamada do worker dele dá rc 1). Abrir não derruba (L9) | 🟡 o worker reconecta à mesma instância (`reconnect_same`) e repete a op 1× (só leitura ou entrada recusada na 1ª chamada); entrada parcial levanta com `delivery` | `_worker/engine.py`, `_worker/dispatch.py` (A.14 n) |
| N2 | **A** | **`WD_QuerySessions` trava** quando chamado enquanto um emulador encerra — e, intermitente, mesmo depois de o processo dele sair (L1/L3) | 🟡 o `close_all` espera a saída dos processos sem chamar a DLL e dispensa a consulta quando a instância conhecida foi fechada; limite de 8 s no watchdog. Resta: se a consulta for necessária e travar, custa 8 s + um worker novo | `_launcher.close_all` (passos 5b/6), `_worker/watchdog.LIMITS` (A.14 l/m) |
| N3 | M | O processo do launcher tem uma `Internet Explorer_Hidden` **visível** e sem título; o `close` mandava `WM_CLOSE` nela também | ✅ janelas auxiliares ignoradas (`HELPER_CLASSES`) | `_instances.py` |
| N4 | M | Com o modal "Short Name already in use" aberto, `WM_CLOSE` derrubou o launcher (0xC0000005); a 2ª instância da mesma letra às vezes cai ao fechar mesmo com o OK antes (L2) | 🟡 o `close` clica OK antes do `WM_CLOSE`; a queda restante é defeito do RUMBA, sem efeito nas outras instâncias | `_launcher.close_all` (passo 3b) |
| N5 | B | Um processo de launcher **por perfil** (D e Z em processos diferentes) | ✅ fato incorporado (instância nova por diferença de conjunto + HWND) | `_launcher`, `_instances` |
| N6 | B | Tempo de registro da letra varia (~9 a ~25 s) e o de conexão (~11 a ~27 s) | ✅ prazo do `open` 90 s (`RUMBAPY_OPEN_TIMEOUT`) e fases no `LaunchTimeout` | `_launcher` |
| N7 | — | Validado sem problema: cp1252 ida e volta (L4), lease/evict/reciclagem entre processos (L3), `show`/`minimize` com leituras (L5), logon CICS com senha em campo oculto em 1,2 s e fechamento logado sem diálogo (L6), neutralização de um servidor 0.1.5 real (L8), script 0.1.5 sem alteração (`RumbaClient("D").logon_cics` → `copy_ps_to_string` → `screen_load` → `close_terminal`) | ✅ | `docs/RUMBA_EHLLAPI.md` §11.1 |

### B.2 Limites assumidos da proteção de conteúdo oculto (A.9 h)

* `secret=True` protege a **leitura** só até a resposta da próxima AID: num campo **visível**, o span secreto cai quando
  o host responde, e a tela passa a mostrar o que o host mandou. Senha vai em campo oculto (README; as receitas
  conferem `Field.hidden` antes de escrever).
* A detecção de escrita espontânea do host (sem AID nossa) num campo oculto só existe no `snapshot` (hash do PS);
  `read`/`read_field` não reenumeram por escrita sem AID.
* Sem como conferir a máscara, a leitura é recusada (`HllapiError` "…para não expor conteúdo oculto") e o `Screen`
  vem com `hidden_unknown=True` (o cliente não anexa essa tela a exceções).

### B.3 Riscos e lacunas remanescentes

| # | Sev | Item | Status |
|---|---|---|---|
| R1 | **A** | Sem rede/GitHub não há uso (fail-closed; sem válvula offline) | ⏳ decisão Q1/D1 |
| R2 | M | `UpdateRequired` uma vez por deploy (tarefas agendadas falham uma execução) | ⏳ decisão Q2 (reexecução adiada para a v2.1) |
| R3 | M | `open`/`close` de um script interrompem o lote de outro na mesma letra (`SessionReplaced`) | ✅ comportamento pedido pelo dono (Q5) |
| R4 | M | `NORESET` muda o comportamento da 0.1.5 (AUTORESET): teclado bloqueado agora levanta `KeyboardLocked` | ✅ decisão Q4; dica de `Key.RESET`, que funciona: `press(Key.RESET)`, `<Reset>` no início de um trecho de `send_keys` e `send_key('RESET')` mandam o `@R` sem esperar o teclado livre (só o fim do `X SYSTEM`); nenhum outro caminho manda RESET (`tests/security/test_tsafe2_aids.py`) |
| R5 | M | Bits do OIA não validados (G1 SSCP-LU, bits de G8, insert, erro de comunicação): só "G8 ≠ 0 = bloqueado" decide | ⏳ exige provocar estados no host (proibido sem plano) |
| R6 | M | rc 11 depois de um `os._exit` do watchdog (conexão do worker anterior presa): tolerado por 15 s na reconexão | ✅ validado ao vivo em 2026-10-04 (sonda avulsa anterior do L3b): depois de `os._exit(3)` sem `DisconnectPS`, o `ConnectPS` seguinte deu rc 0 em ~14 ms, sem rc 11. **Ressalva:** falta rodar a variante nova `tests/live` (`--script l3b`, com `GuardedDll` e confirmação no console); o `PLANO_DE_MELHORIAS.md` §8 diz o mesmo |
| R7 | M | Breakaway do Job negado (IDE) ⇒ worker morto à força ao fechar a IDE ⇒ 1 slot HLLAPI vaza | 🟡 WARNING no log; `ClientTableFull` com instrução (logoff) |
| R8 | M | Abas do Edge no modo IE seguram a letra | 🟡 `InstanceNotClosable` com a dica de fechá-las |
| R9 | B | Janela TOCTOU estreita entre a conferência de `Session.close()` (`status`) e o `close_all` | 🟡 aceito (A.14 e) |
| R10 | B | `.SHARDAT` global em máquina multiusuário (colisão de `LOWORD(pid)`) | 🟡 colisão detectada no boot (saída 15) e novo spawn; neutralização só na própria sessão do Windows |
| R11 | B | Conjunto seguro de caracteres por host (`€` no IMS, `¤` no CICS) validado só no D | ⏳ L4 só em D |
| R12 | B | Logon IMS (`Ims.logon`/`logon_rhelp`) com as âncoras da 0.1.5, não validado ao vivo | ⏳ L7 exige autorização explícita |
| R13 | B | Mensagens provisórias fora dos templates de `errors.py`: `LaunchFailed` do fallback IE ("O Internet Explorer não abriu o {perfil} ({step}: {error})."), `WorkerStartFailed` sem código de saída ("…não iniciou: {motivo}") | ⏳ variantes `ie`, `too_many`/`popen_failed`/`not_ready` (A.10 f, A.12 f) |
| R14 | B | `HostBusy` numa **leitura** usa o texto "parte das teclas pode ter sido digitada", enganoso | ⏳ variante `read` (A.11 h) |
| R15 | B | `UpdateRequired` "Outro processo atualizou o RumbaPy. Rode de novo." não usa a ação do contexto (Jupyter/REPL) | ⏳ variante `other_process_action` (A.14 b) |
| R16 | B | Repositório público com a engenharia reversa da DLL | ⏳ decisão Q8/D7 (jurídico/Segurança) |
| R17 | B | O exemplo de Jupyter da especificação (B.10) usa `t.screen.input_fields`, mas `t.screen` não traz campos (`ParameterError`); o README usa `t.snapshot(fields=True).input_fields` | ⏳ corrigir o texto da B.10 |
| R18 | B | `attach`/`RumbaClient.connect()` (e o anexo automático da compat) num terminal com o teclado inibido por erro de operador (`X -f`) dão `LaunchTimeout` "o host não mandou a primeira tela" (`_launcher._looks_ready` exige `oia.ready`): um script novo não consegue anexar para mandar o RESET; só `open` (fecha e reabre) ou o Reset na janela | ⏳ proposta de aceitar a 1ª tela com `X -f` no `attach` (decisão do dono, E.6) |

## C. Pendências do dono antes do merge no `master`

1. **Decisões K.2** (padrão adotado entre colchetes): Q1 válvula offline [não existe]; Q2 reexecução após update
   [não]; Q3 mudanças de semântica da compat [aceitar]; Q4 `NORESET` [sim]; Q5 `open`/`close` revogam o lease de outro
   script [sim]; Q6 proteção do `master` + CI obrigatório [recomendado]; Q7 download do Python 32-bit [v2.1];
   Q8 publicação da engenharia reversa [pendente]; Q9 testes ao vivo além do permitido [só com plano]; Q10 canal `dev`
   [não]; Q11 `with` fecha o terminal [não; `close_on_exit=True`].
2. Variantes de mensagem em `errors.py`: `HostBusy` `read` (R14), `UpdateRequired` `other_process_action` (R15),
   `LaunchFailed` `ie` e `WorkerStartFailed` `too_many`/`popen_failed`/`not_ready` (R13).
3. **Teste manual do `heal`** (H.6) registrado no PR antes do merge (obrigatório também a cada mudança do
   `_bootstrap.py`, cujo hash está em `_build/bootstrap.sha256`).
4. Job **`install-smoke` verde no GitHub Actions** (localmente roda com `RUMBAPY_SMOKE=1`), e o restante da matriz do CI.
5. Roteiros ao vivo: **L7 (RHELP) não validado** (exige autorização explícita); **L3b validado ao vivo em 2026-10-04**
   pela sonda avulsa anterior (`ConnectPS` rc 0 em ~14 ms depois de `os._exit(3)` sem `DisconnectPS`, sem rc 11); a
   variante nova de `tests/live` (`--script l3b`, guard readonly e watchdog no processo de teste) e os demais roteiros
   pelo executor novo (relatório com registro do guard e varredura de senha) ainda não foram executados.
6. Portão do `master` (J.1): CI verde em `dev`, roteiro L1–L6 executado pelo dono em `dev`, versão incrementada,
   `CHANGELOG.md`. Depois do merge: avisar os usuários para reiniciar kernels/scripts antigos (H.8).

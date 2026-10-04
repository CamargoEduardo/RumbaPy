# tools/research — ferramentas de investigação do RUMBA

Scripts usados na engenharia reversa documentada em [`docs/RUMBA_EHLLAPI.md`](../../docs/RUMBA_EHLLAPI.md)
e [`docs/RUMBA_AMBIENTE.md`](../../docs/RUMBA_AMBIENTE.md). **Não fazem parte do pacote `rumbapy`**
(o `pyproject.toml` só empacota `src/`). Os da tabela abaixo são **somente leitura**: não conectam, não digitam,
não alteram registro nem instalação. Os roteiros `live_*.py` (seção seguinte) abrem o terminal real.
Toda sonda `live_*_probe.py` é só um invólucro do executor `tests/live/runner.py` (nenhuma carrega a DLL, abre o
launcher ou manda mensagem a janela por conta própria; `tests/security/test_live_safeguards.py` confere).

| Script | Python | Dependências | O que faz |
|---|---|---|---|
| `pe_exports.py` | qualquer | stdlib | Lista exports/imports/versão de DLLs PE. `python pe_exports.py ve <dll...>` (`v`=versão, `e`=exports, `i`=imports) |
| `rdis.py` | 64-bit (venv) | `pefile`, `capstone` | Desmonta exports/endereços com anotação de imports e strings; `--range` e `--xref` |
| `probe_hllapi.py` | **32-bit** | stdlib | `WD_QuerySystem`, `WD_QuerySessions`, `WD_QuerySessionStatus` (sessões publicadas) |
| `enum_windows.py` | qualquer | stdlib | Árvore de janelas de um PID (achar o emulador dentro do IE/Edge) |
| `dde_list.py` | qualquer | stdlib | Lista conversas DDE disponíveis (procure `RUMBADSP`) |

## Roteiros ao vivo da v2 (terminal de PRODUÇÃO)

Estes scripts **abrem o RUMBA real** (Web Launcher + `ehlapi32.Dll`). Só o agente principal ou o dono os executam,
com autorização, dentro do permitido na tabela J.6 de [`docs/ESPECIFICACAO_V2.md`](../../docs/ESPECIFICACAO_V2.md).
Todos: exigem `RUMBAPY_LIVE=1`; testam o código do `HEAD` exportado por `git archive` (edições em andamento não
interferem); usam `RUMBAPY_DEV=1` e `RUMBAPY_HOME` temporário; ligam o `GuardedDll` no worker; recusam rodar se já
houver janela do perfil aberta; pedem a confirmação `SIM <letra>` digitada no console (`CONIN$`; sem console, recusam;
nenhuma variável de ambiente confirma no lugar da pessoa); gravam o relatório JSON em `docs/interno/referencia/` (fora
do Git), com o registro do guard tirado do log do worker (AIDs, `op=connect` com `rc`, NORESET); apagam o
`RUMBAPY_HOME` temporário no fim (`--keep-home` mantém); nunca mostram credenciais (só digitadas, senha sem eco).

| Script | Guard | O que faz |
|---|---|---|
| `live_v2_probe.py --script l1` | readonly | `open("D", window="minimized")`, tempos, campos, cursor, leitura, latência do `snapshot`, `close` |
| `live_v2_probe.py --script l2` | readonly | 2ª instância pelo launcher → "Short Name" → `open("D")` fecha as duas |
| `live_v2_probe.py --script l3` | readonly | lease entre processos, `TerminalInUse`, `close` com dono vivo (evict), reciclagem do worker |
| `live_v2_probe.py --script l3b` | readonly | conexão HLLAPI própria (32-bit, mesma DLL e `hllapi.py`, `GuardedDll` readonly) sai por `os._exit(3)` sem `DisconnectPS`; rc/tempo do `ConnectPS` do worker novo (R8) |
| `live_v2_probe.py --script l4` | text | escrever e apagar no PS local (cada caractere de `TESTE çãÉ€`, ida e volta conferida), `Truncated`/`ProtectedField`; nenhuma AID |
| `live_v2_probe.py --script l5` | readonly | `show`/`minimize` com leituras entre eles e o título da janela em cada estado |
| `live_v2_probe.py --script l6` | login | logon CICS com usuário/senha digitados no console; linha do tempo do OIA após cada ENTER; âncoras (MENU/SIGNON/OK/MSG); varre a senha em logs/reprs |
| `live_v2_probe.py --script l8` | readonly | o mesmo que `live_legacy_probe.py` (servidor 0.1.5 real, sem terminal) |
| `live_v2_probe.py --script l9` | readonly | `BRAZIL_Z` aberto + `open("D")`: processos, leituras do Z antes/depois de abrir e de fechar o D |
| `live_v2_probe.py --script compat` | login | script da 0.1.5 sem alteração: `RumbaClient("D").logon_cics` → `copy_ps_to_string` → `screen_load` → `close_terminal` |
| `live_worker_probe.py [--write-erase]` | readonly / text | invólucro: `--script l1` (sem opção) ou `--script l4` (`--write-erase`) |
| `live_l3b_probe.py` | readonly | invólucro: `--script l3b` (a versão avulsa executada em 2026-10-04 está no histórico do Git) |
| `live_close_probe.py` | readonly | inventário de janelas do processo do launcher; `WM_CLOSE` só na principal; quando a DLL volta a responder |
| `live_legacy_probe.py` | — | servidor 0.1.5 REAL (venv 32-bit à parte) na 8500 → `_legacy.neutralize("D")` (L8) |
| `live_session_probe.py` | readonly | invólucro: `--script l5` (a versão 32-bit da 1ª validação, com `WD_*` diretos, está no histórico do Git) |

Exemplo (Python 64-bit do repositório):

```bat
set RUMBAPY_LIVE=1
.venv\Scripts\python.exe tools\research\live_v2_probe.py --script l1
```

Proibido sem plano escrito aprovado pelo dono: L7 (IMS/RHELP), cancelar o "Connecting" à mão, provocar `X -f`,
qualquer PF/PA/transação ou escrita em tela de negócio.

## Preparar um venv descartável para o `rdis.py`

```bash
python -m venv %TEMP%\rumba_re && %TEMP%\rumba_re\Scripts\pip install pefile capstone
%TEMP%\rumba_re\Scripts\python tools\research\rdis.py "C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll" WD_ConnectPS WD_SendKey
%TEMP%\rumba_re\Scripts\python tools\research\rdis.py --range "...\ehlapi32.Dll" 100034d0 10003c80   # despachante interno
%TEMP%\rumba_re\Scripts\python tools\research\rdis.py --xref  "...\WHLLAPI.dll" WaitForSingleObject
```

## Endereços úteis em `ehlapi32.Dll` 10.2.1710 (ImageBase 0x10000000)

| Endereço | O quê |
|---|---|
| `0x100034d0` | despachante comum de todas as `WD_*`: `dispatch(hInst, func, pOut, pos, len, data)` |
| `0x10003e60` | *switch* por função (lado cliente) |
| `0x10003d50` | envio: `CreateEvent("Global\…")` + `PostMessage("WDHLLAPI SEND")` + `WaitForSingleObject(INFINITE)` |
| `0x1000a870` | `CreateFileMapping("HLLAPIMem%lX")` |
| `0x1000bbc0` | ID do cliente = `LOWORD(PID) | (multithread ? LOWORD(TID) : 1) << 16` |
| `0x1000bca0` | slot local (32) → `MessageBox` "Exceeded maximum number of clients" se esgotar |
| `0x100030f0` | init: `RegisterWindowMessage("WDHLLAPI SEND"/"WDHLLAPI TERMINATE")` |
| `0x10006fd0` | `APIRegisterApp(hwnd, letra, fmt)` — chamado pelo emulador |
| `0x1000c670` | `DllMain` (lê `DISPLAY_PROCESS_ID`; limpeza no detach) |
| `0x1000eb10` | tabela de nomes/bits do `WD_SetSessionParameters` |
| `.SHARDAT 0x10016000` | memória compartilhada entre processos (tabelas de sessão e clientes) |

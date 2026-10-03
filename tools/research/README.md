# tools/research — ferramentas de investigação do RUMBA

Scripts usados na engenharia reversa documentada em [`docs/RUMBA_EHLLAPI.md`](../../docs/RUMBA_EHLLAPI.md)
e [`docs/RUMBA_AMBIENTE.md`](../../docs/RUMBA_AMBIENTE.md). **Não fazem parte do pacote `rumbapy`**
(o `pyproject.toml` só empacota `src/`). Todos são **somente leitura**: não conectam, não digitam,
não alteram registro nem instalação.

| Script | Python | Dependências | O que faz |
|---|---|---|---|
| `pe_exports.py` | qualquer | stdlib | Lista exports/imports/versão de DLLs PE. `python pe_exports.py ve <dll...>` (`v`=versão, `e`=exports, `i`=imports) |
| `rdis.py` | 64-bit (venv) | `pefile`, `capstone` | Desmonta exports/endereços com anotação de imports e strings; `--range` e `--xref` |
| `probe_hllapi.py` | **32-bit** | stdlib | `WD_QuerySystem`, `WD_QuerySessions`, `WD_QuerySessionStatus` (sessões publicadas) |
| `enum_windows.py` | qualquer | stdlib | Árvore de janelas de um PID (achar o emulador dentro do IE/Edge) |
| `dde_list.py` | qualquer | stdlib | Lista conversas DDE disponíveis (procure `RUMBADSP`) |

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

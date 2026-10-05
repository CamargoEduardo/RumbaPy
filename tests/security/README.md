# tests/security — testes de segurança transversais (J.5)

Nenhum destes testes toca o RUMBA, a DLL real ou o mainframe: rodam no sandbox do `tests/conftest.py` (J.2), no CI e
nos dois interpretadores (x64 e x86).

| Teste | Onde está | O que garante |
|---|---|---|
| **T-SEC-1** senha nunca aparece | `tests/e2e/bridge/test_security_e2e.py` (ponta a ponta: worker 32-bit real + fake DLL, espião no pipe, logs em DEBUG, `repr`/exceções, senha com `<`, `send_keys` sem `secret`); partes unitárias em `tests/unit/core/test_keys.py`, `tests/unit/client/session/test_session_io.py` e `tests/unit/contracts/test_screen_contract.py` | a senha não sai do cliente fora de `write`/`send` ao worker |
| **T-SEC-2** sem dados internos | `test_tsec2_internal_data.py` | nenhum arquivo versionado (ou novo e não ignorado) contém os padrões internos; padrões do segredo do CI `RUMBAPY_TSEC2_PATTERNS` (separados por `;`) ou de `docs/interno/tsec2_patterns.txt` (fora do Git); sem padrões, pulado. Só o nome do arquivo aparece na falha |
| **T-SAFE-1** sandbox | sentinelas do `tests/conftest.py` + `tests/unit/contracts/test_sandbox.py` | DLL HLLAPI, mensagens/ativação de janelas reais, processos do RUMBA/launcher/IE/cscript/shell/taskkill, `os.kill` fora dos filhos, rede fora do loopback e portas da v0.1.5 são bloqueados |
| **T-SAFE-2** nenhuma AID a mais | `test_tsafe2_aids.py` | `aid_log` exato por API (`press`/`enter`/`clear`/`pf`/`pa`, `send_keys`, receitas, compat `send_key`/`screen_load`…); `send_key("A@EB@E")` = 1 AID; `PAGE_DOWN` conta; cada `WD_SendKey` tem no máximo uma AID, no fim |
| **T-SAFE-3** nada antes da tela | `test_tsafe3_screen_first.py` (+ `test_session_io.py` e `tests/unit/client/launcher/test_open.py`) | `send_keys("C<Enter>x<Enter>")` com `response_delay`/`lock_visible_after` só digita na tela nova; `rumbapy.open` com `blank_first_screen(2)` só devolve depois da 1ª tela |
| Salvaguardas dos roteiros ao vivo | `test_live_safeguards.py` | decisão de pular (`RUMBAPY_LIVE`, sandbox, 64-bit, branch `dev`, L7 autorizado, teto `RUMBAPY_GUARD`), confirmação `SIM <letra>` só pelo console (nenhum atalho por ambiente/stdin), credenciais só digitadas, varredura e redação da senha, registro do guard pelo log do worker (AIDs por roteiro, `op=connect`, NORESET), aceite do relatório (inclusive L1 `windows_after_close`, L3 evict, L3b, limpeza que encobre falha) e recusas de `tests/live/runner.py` |

Os roteiros ao vivo propriamente ditos (L1–L9, L3b e compat, J.6) ficam em `tests/live/` e só rodam à mão, na máquina do
dono, com `RUMBAPY_LIVE=1` (nunca no CI: `-m "not live"`).

# INDEX — mapa do repositório RumbaPy

## Comece por aqui
| Se você quer… | Leia |
|---|---|
| Regras para mexer no código (agentes) | [`CLAUDE.md`](CLAUDE.md) |
| Entender como funciona hoje e por quê | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Saber o que está quebrado/arriscado | [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) |
| O plano de evolução (arquitetura-alvo, fases, decisões) | [`docs/PLANO_DE_MELHORIAS.md`](docs/PLANO_DE_MELHORIAS.md) |
| Tudo sobre a `ehlapi32.Dll` (funções, IPC, threads, rc, teclas) | [`docs/RUMBA_EHLLAPI.md`](docs/RUMBA_EHLLAPI.md) |
| O RUMBA instalado, portal, perfis, hosts, COM, janelas | [`docs/RUMBA_AMBIENTE.md`](docs/RUMBA_AMBIENTE.md) |
| Reproduzir a engenharia reversa | [`tools/research/README.md`](tools/research/README.md) |
| Entender o jargão (PS, OIA, AID, LU, EAB, CCSID…) | [`docs/GLOSSARIO.md`](docs/GLOSSARIO.md) |
| Dados internos (hosts, dumps de TLB/.NET, páginas do portal, manual WD_API em PDF) — **local, não versionado** | `docs/interno/` |
| Instalar/usar como usuário final | [`README.md`](README.md) |

## Fatos rápidos
| | |
|---|---|
| Pacote | `rumbapy` 0.1.5 (`pyproject.toml`), instalado de `git+https://github.com/CamargoEduardo/RumbaPy.git` |
| API pública | `from rumbapy import RumbaClient` |
| Python | usuário 64-bit ≥ 3.12; servidor **32-bit ≥ 3.13** |
| DLL | `C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll` 10.2.1710 (x86) |
| Portal | `https://rumba.srv.volvo.com/rumba694/w2hlegacy/index_pro_api.html` |
| Sessões | `D`=BRAZIL_D, `Z`=BRAZIL_Z (CICS, host CICS-BR, 24×80, EBCDIC 1143); `A`=IMS_A (RHELP, host IMS-SE, 32×80, EBCDIC 037) |
| Dados internos | `docs/interno/` — **não versionado** (o repositório GitHub é público) |
| Portas locais | `D=8500`, `A=8600`, `Z=8700`, outros `8800` |
| Auto-update | **obrigatório**; compara commit instalado × `git ls-remote HEAD` |

## Arquivos
| Caminho | O quê |
|---|---|
| `src/rumbapy/__init__.py` | Exporta `RumbaClient` |
| `src/rumbapy/client.py` | Lado 64-bit: auto-update, `find_python_32bit`, `RumbaClient` (proxy TCP JSON) |
| `src/rumbapy/server.py` | Lado 32-bit: servidor TCP, `execute_command`, `logon_cics`, `logon_rhelp`, `screen_load` |
| `src/rumbapy/api.py` | Lado 32-bit: `RumbaAPI` (ctypes `WD_*`), abertura via IE COM, `close_terminal`, `KEYS`, `ERROR_CODES` |
| `pyproject.toml` | Metadados; extra `server32 = pywin32, pywinauto` |
| `config.example.ini` | Modelo legado (credenciais/caminhos) — não é lido pelo código atual |
| `src/rumbapy.egg-info/` | Artefato de build versionado por engano (desatualizado) |
| `tools/research/*.py` | `pe_exports`, `rdis` (disassembler), `probe_hllapi` (32-bit), `enum_windows`, `dde_list` |

## Índice de símbolos (commit `5c03d47`)
| Símbolo | Local |
|---|---|
| `RumbaClient.__init__` | `src/rumbapy/client.py:252` |
| `RumbaClient._start_server` | `src/rumbapy/client.py:277` |
| `RumbaClient._send_command` | `src/rumbapy/client.py:312` |
| `find_python_32bit` | `src/rumbapy/client.py:39` |
| `update_python_32bit` / `ensure_python_32bit` | `src/rumbapy/client.py:75` / `:30` |
| `update_python_64bit` | `src/rumbapy/client.py:195` |
| `start_server` | `src/rumbapy/server.py:306` |
| `handle_client` | `src/rumbapy/server.py:250` |
| `execute_command` | `src/rumbapy/server.py:162` |
| `logon_cics` / `logon_rhelp` | `src/rumbapy/server.py:63` / `:121` |
| `screen_load` / `is_in_erro_grave` | `src/rumbapy/server.py:29` / `:22` |
| `RumbaAPI` | `src/rumbapy/api.py:158` |
| `RumbaAPI.navigate_to_terminal` | `src/rumbapy/api.py:174` |
| `RumbaAPI.close_terminal` | `src/rumbapy/api.py:240` |
| `RumbaAPI.wd_*` | `src/rumbapy/api.py:262-333` |
| `timeout` (decorator) | `src/rumbapy/api.py:132` |
| `KEYS` / `ERROR_CODES` / `RumbaConfig` | `src/rumbapy/api.py:92` / `:17` / `:125` |

## Telas e coordenadas usadas pelos fluxos (linha, coluna)
| Fluxo | Passo | Posição | Texto |
|---|---|---|---|
| CICS | aguarda menu | (14,33) | `CICS` |
| CICS | escolhe aplicação | (22,56) | digita `C` + ENTER |
| CICS | aguarda signon | (4,6) | `TERMINAL` |
| CICS | usuário / senha | (12,21) / (13,21) | uid / pwd + ENTER |
| CICS | erro de senha | (19,19) len 14 | `SENHA EXPIRADA` / `SENHA INVALIDA` |
| CICS | sucesso | (5,20) | `Signon` |
| CICS | erro grave | (5,31) | `E R R O   G R A V E` |
| IMS/RHELP | tela USS | (2,1) | `USS010` → digita `IMSRS` em (5,1) |
| IMS/RHELP | boas-vindas | (4,44) | `Welcome` → uid (10,47), pwd (11,47) |
| IMS/RHELP | menu | (16,20) | `Start` → digita `RHELP` em (16,47) |
| CICS (histórico) | SCOM | (1,2) `SCOM` → (20,2) `Funcao` → (20,11) `A` | removido em `a2133f1` |
| CICS (histórico) | SCPE | (1,2) `SCPE` → (23,7) `INFORME` → (10,11) `ALT/INC/EXC` → (2,2) `VOLVO` | removido em `a2133f1` |
| CICS (exemplo `run.py`) | SPRE | (1,2) `SPRE` → (1,2) `VOLVO` → (22,10) `P` → (1,74) `SP3114T` | `git show 691c8c2:run.py` |

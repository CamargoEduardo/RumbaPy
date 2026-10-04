# INDEX — mapa do repositório RumbaPy

## Comece por aqui
| Se você quer… | Leia |
|---|---|
| Instalar/usar como usuário final | [`README.md`](README.md) |
| Regras para mexer no código (agentes) | [`CLAUDE.md`](CLAUDE.md) |
| Entender como a v2 funciona e por quê | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| O contrato exato (constantes, mensagens, protocolo, testes) | [`docs/ESPECIFICACAO_V2.md`](docs/ESPECIFICACAO_V2.md) — A.8–A.14 prevalecem |
| O que mudou desde a 0.1.5 (inclusive na compat) | [`CHANGELOG.md`](CHANGELOG.md) |
| O que está resolvido, o que está aberto, o que depende do dono | [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) |
| Tudo sobre a `ehlapi32.Dll` (funções, IPC, threads, rc, teclas, medições ao vivo) | [`docs/RUMBA_EHLLAPI.md`](docs/RUMBA_EHLLAPI.md) |
| O RUMBA instalado, portal, perfis, processos/janelas, COM | [`docs/RUMBA_AMBIENTE.md`](docs/RUMBA_AMBIENTE.md) |
| O plano original (histórico) e o que foi feito/adiado | [`docs/PLANO_DE_MELHORIAS.md`](docs/PLANO_DE_MELHORIAS.md) |
| Reproduzir a engenharia reversa e os roteiros ao vivo | [`tools/research/README.md`](tools/research/README.md) |
| Entender o jargão (PS, OIA, AID, LU, EAB, worker, lease, evict…) | [`docs/GLOSSARIO.md`](docs/GLOSSARIO.md) |
| Dados internos (hosts, achados ao vivo, dumps de TLB/.NET, páginas do portal, manual WD_API, proposta e críticas da v2) — **local, não versionado** | `docs/interno/` |

## Fatos rápidos
| | |
|---|---|
| Pacote | `rumbapy` **2.0.0** (`pyproject.toml` = `_version.py`), branch `dev`; o `master` ainda tem a 0.1.5 |
| Instalação | `python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip` (também o comando de recuperação) |
| API pública | `rumbapy.open/attach/close`, `Session`, `Screen`/`Field`/`Cursor`/`OIA`, `Key`, `doctor`, exceções de `errors.__all__`; compat `RumbaClient` |
| CLI | `python -m rumbapy {version | update | doctor [--verbose] | close <L>}` |
| Python | usuário 64-bit ≥ 3.12; worker **32-bit ≥ 3.12** (nada instalado nele; roda dos arquivos do pacote 64-bit) |
| DLL | `C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll` 10.2.1710 (x86) |
| Abertura | Host Access Web Launcher (`hawlauncher://<portal>/profpro/<PERFIL>.html/microfocus_w2h_hawlauncher`); IE só de reserva |
| Sessões | `D`=BRAZIL_D, `Z`=BRAZIL_Z (CICS, host CICS-BR, 24×80, EBCDIC 1143); `A`=IMS_A (RHELP, host IMS-SE, 32×80, EBCDIC 037) |
| Canal | named pipe `\\.\pipe\rumbapy2-<tag>-<L>-<rand>`, DACL do usuário + HMAC mútuo, JSON, protocolo 2 |
| Dados locais | `%LOCALAPPDATA%\rumbapy\` (`RUMBAPY_HOME`): `run\`, `logs\`, `locks\`, `authkey.bin`, `update-state.json`, `runtime.json` |
| Auto-update | **obrigatório**; commit instalado × `refs/heads/master` (HTTPS `info/refs`, reservas `git ls-remote` e API); sem rede bloqueia; `heal` no import |
| Portas legadas | `D=8500`, `A=8600`, `Z=8700`, outros `8800` — só para achar e neutralizar servidores 0.1.5 (e o stub `rumbapy.server`) |
| Dados internos | `docs/interno/` — **não versionado** (o repositório GitHub é público) |

## Arquivos
| Caminho | O quê |
|---|---|
| `src/rumbapy/__init__.py` | Forma literal da A.6: update, imports ansiosos, `BUILD_ID`, heal; exporta a API |
| `src/rumbapy/__main__.py` | CLI (`version`, `update`, `doctor`, `close`) |
| `src/rumbapy/session.py` | `open`, `attach`, `close`, `Session` |
| `src/rumbapy/screen.py` / `keys.py` / `waits.py` | `Screen`/`Field`/`Cursor`/`OIA`; `Key`, tokenizadores, chunks; motor de espera |
| `src/rumbapy/recipes/cics.py` / `ims.py` | `Cics.logon/start_transaction/check`; `Ims.logon` |
| `src/rumbapy/errors.py` | Hierarquia `RumbaError` com templates/hints e `to_wire/from_wire` |
| `src/rumbapy/compat.py` / `client.py` / `server.py` / `api.py` | `RumbaClient` (0.1.5); módulo legado; stub do servidor; stub `ImportError` |
| `src/rumbapy/_launcher.py` / `_instances.py` / `_user32.py` / `_legacy.py` / `_ie_open.js` | Abrir/anexar/fechar no cliente; classificação de janelas; user32; servidores 0.1.5; fallback IE |
| `src/rumbapy/_bridge.py` / `_backend.py` / `_ipc.py` / `_protocol.py` | Workers do lado cliente; `Backend`; pipe+HMAC; tabela `OPS` |
| `src/rumbapy/_bootstrap.py` / `_update.py` / `_role.py` / `_buildid.py` / `_version.py` | Auto-update (mecanismo congelado + política); papel; build_id; versão |
| `src/rumbapy/_runtime32.py` / `_doctor.py` | Python 32-bit; diagnóstico |
| `src/rumbapy/_paths.py` / `_locks.py` / `_log.py` / `_codec.py` / `_types.py` / `_win32.py` / `profiles.py` | Caminhos e nomes; travas; logs; texto/posições; tipos de wire; kernel32/advapi32; perfis |
| `src/rumbapy/_worker/` | `main` (boot), `server` (threads/lease), `dispatch`, `engine`, `hllapi`, `rc`, `structs`, `mask`, `watchdog`, `guard` |
| `_build/rumbapy_build.py`, `_build/bootstrap.sha256` | Backend PEP 517 só stdlib; hash do `_bootstrap.py` congelado |
| `.github/workflows/ci.yml` | ruff + pyright; pytest 3.12–3.14 × x64/x86; install-smoke |
| `tests/conftest.py` | Sandbox + sentinelas (J.2) |
| `tests/fakes/` | `FakeEhllapi`, `FakeUser32`, `scenarios`, `InProcessBackend`, reexport do guard |
| `tests/unit/` | `core`, `contracts`, `worker`, `ipc`, `client/{session,launcher,bridge,recipes,compat}`, `update`, `test_layering.py`, `test_contracts.py` |
| `tests/e2e/` | `bridge/` (worker 32-bit real com `--fake-dll`: pipe, lease, evict, reciclagem, colisão, segurança), `update/` (GitHub/pip falsos, heal, install-smoke) |
| `tests/live/` | Roteiros ao vivo (manuais; `RUMBAPY_LIVE=1` + guard) |
| `tests/security/` | T-SEC-2 (padrões internos), T-SAFE-1..3, salvaguardas dos roteiros ao vivo; o T-SEC-1 de ponta a ponta fica em `tests/e2e/bridge/test_security_e2e.py` (J.5) |
| `tools/research/*.py` | `pe_exports`, `rdis`, `probe_hllapi`, `enum_windows`, `dde_list`; sondas ao vivo `live_*` |
| `config.example.ini` | Modelo legado (não é lido pelo pacote; o `config.ini` real é só para os roteiros ao vivo e está no `.gitignore`) |

## Onde está cada coisa (símbolos)
| Símbolo | Arquivo |
|---|---|
| `open` / `attach` / `close` / `Session` | `src/rumbapy/session.py` |
| `RumbaClient`, `Mnemonic` | `src/rumbapy/compat.py` |
| `Launcher.open` / `.attach` / `.close_all`, `open_terminal`, `close_all` | `src/rumbapy/_launcher.py` |
| `classify`, `sanitize`, `HELPER_CLASSES` | `src/rumbapy/_instances.py` |
| `neutralize` | `src/rumbapy/_legacy.py` |
| `get_worker`, `spawn`, `handshake`, `WorkerHandle`, `BOOT` | `src/rumbapy/_bridge.py` |
| `Backend`, `WorkerBackend` | `src/rumbapy/_backend.py` |
| `OPS`, `validate_request` | `src/rumbapy/_protocol.py` |
| `ensure_latest`, `install_mode`, `status` | `src/rumbapy/_update.py` |
| `remote_master_sha`, `pip_install`, `heal`, `RECOVERY_COMMAND` | `src/rumbapy/_bootstrap.py` |
| `find` (Python 32-bit) | `src/rumbapy/_runtime32.py` |
| `doctor` | `src/rumbapy/_doctor.py` |
| `wait_until`, prazos e `cics_error_check` | `src/rumbapy/waits.py` |
| `Cics`, `Ims` | `src/rumbapy/recipes/` |
| worker: `main`, `Engine` (`reconnect_same`), `Dispatcher`, `Hllapi`/`load_dll`, `MaskState`, `Watchdog` (`LIMITS`), `GuardedDll` | `src/rumbapy/_worker/` |

## Telas e coordenadas usadas pelas receitas (linha, coluna)
| Fluxo | Passo | Posição | Texto |
|---|---|---|---|
| CICS | menu | linha 14 (começa em (14,26)) | `Select CICS Environment` — validado ao vivo |
| CICS | escolhe o ambiente | (22,56) desprotegido | `C` + ENTER |
| CICS | signon | (4,6) | `TERMINAL` — validado ao vivo (L6) |
| CICS | usuário / senha | (12,21) / (13,21) **oculto** | uid / pwd (`secret=True`) + ENTER |
| CICS | erro de senha | (19,19) | `SENHA EXPIRADA` / `SENHA INVALIDA` |
| CICS | sucesso | (5,20) | `Signon` — validado ao vivo (1,2 s) |
| CICS | erro grave | (5,31) | `E R R O   G R A V E` (detector em todas as esperas de perfis CICS) |
| IMS/RHELP | tela USS | (2,1) | `USS010` → `IMSRS` em (5,1) — **não validado na v2 (L7)** |
| IMS/RHELP | boas-vindas | (4,44) | `Welcome` → uid (10,47), pwd (11,47) oculto |
| IMS/RHELP | menu | (16,20) | `Start` → `RHELP` em (16,47) |

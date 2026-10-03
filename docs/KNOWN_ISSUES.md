# Problemas conhecidos (auditoria de 2026-10-03, commit `5c03d47`)

Severidade: **C** = crítico (quebra requisito/corrompe/trava), **A** = alto (falha em produção
plausível), **M** = médio (robustez/segurança), **B** = baixo (qualidade).
Referências `arquivo:linha` valem para o commit auditado.

## 1. Requisito "sempre a última versão"

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 1.1 | **C** | `client.py:280-283` | Se já existe um servidor escutando na porta, o cliente o reutiliza **sem checar versão**. Após um auto-update o servidor 32-bit antigo continua vivo (não tem *idle timeout*) e executa **código antigo** indefinidamente. | *Handshake* de versão (commit/`__version__`) no `ping`; se divergir, pedir `shutdown` e subir um novo. |
| 1.2 | **A** | `client.py:195-249` | O update do pacote 64-bit é feito **no interpretador que está rodando**: o processo atual continua com os módulos antigos em memória; a versão nova só vale na próxima execução. | Checar/atualizar antes de importar o resto do pacote e reexecutar (`os.execv`) ou exigir reinício explícito (ver plano, Fase 5). |
| 1.3 | M | `client.py:263-265` | `update_python_64bit()` roda a **cada** `RumbaClient()` (sem guarda), e `git ls-remote` é chamado duas vezes por instanciação → lentidão; exige `git` no PATH; falha de rede derruba a criação do cliente com `CalledProcessError` sem mensagem amigável. | Uma checagem por processo, uma única consulta remota, mensagens claras, política de falha explícita. |
| 1.4 | M | `client.py:255`, `263` | O parâmetro público `auto_update=False` permite a **qualquer usuário** desligar a obrigatoriedade. | Restringir o desligamento a modo de desenvolvimento explícito (ex.: variável `RUMBAPY_DEV=1`, registrado em log) ou remover o parâmetro (mantendo-o aceito e ignorado, por compatibilidade). |

## 2. Corrupção de dados / encoding

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 2.1 | **C** | `api.py:286`, `api.py:311` | `.decode("utf-8")` em texto vindo do emulador, que é **cp1252** (confirmado ao vivo: o byte `0xE7` é exibido como `ç`). Qualquer acento na faixa lida (`Função`, `Usuário`, `ç`) gera `UnicodeDecodeError` e a ação falha. | Decodificar com `cp1252` e `errors="replace"`. |
| 2.2 | **A** | `api.py:293` | `.encode("ascii")` ao escrever: qualquer acento/ç em dados de negócio (nomes, descrições) quebra `copy_string_to_ps`. | `encode("cp1252", errors="replace")`. |
| 2.3 | **A** | `api.py:283-286`, `308-311` | `create_string_buffer(n).value` corta no **primeiro byte NUL**. Campos não preenchidos da tela 3270 contêm `0x00` (o perfil usa `ConvertNulls=false`), então leituras voltam **mais curtas que o pedido** e comparações como as de `screen_load` podem falhar. | Usar `buffer.raw[:n]` e trocar `\0` por espaço. |
| 2.4 | **C** (latente) | `api.py:296-301` | `wd_query_session_status` passa um `bytes` imutável de 1 byte como buffer de saída de **20 bytes** → a DLL escreve fora do objeto (corrupção de heap). Nunca é chamada hoje. | Buffer mutável de 20 bytes com `buf[0]=short name`; parsear o layout 32-bit (ver `docs/RUMBA_EHLLAPI.md`). |
| 2.5 | B | `api.py:303`, `353` | `copy_field` limita a 80 bytes (campos maiores são truncados, rc=6 ignorado); anotação `-> int` mas retorna `str`. | Descobrir o tamanho com `WD_FindFieldLength`. |

## 3. Travamentos e concorrência

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 3.1 | **C** | `api.py:132-156` | Por dentro, a `ehlapi32.Dll` espera a resposta do emulador com `WaitForSingleObject(evento, INFINITE)`. O `@timeout` só **abandona** a thread: ela continua bloqueada, segurando o mutex `PID<pid>` do processo. Depois disso, toda chamada espera 1 s pelo mutex e roda **sem proteção** (a DLL ignora o timeout do mutex). | *Watchdog* que **recicla o processo worker** (saída limpa via `os._exit`, para o `DllMain` rodar o cleanup), em vez de abandonar threads. |
| 3.2 | **A** | `server.py:331-337` | Uma thread por conexão TCP → chamadas concorrentes à DLL vindas de clientes diferentes do mesmo servidor. A identidade HLLAPI é **por processo** (todas as `RumbaAPI` do servidor compartilham **a mesma** conexão), então `disconnect` de um cliente derruba os outros, e o `session_id` não isola nada. | Um worker por sessão, com **uma única thread** que serializa todas as chamadas à DLL. |
| 3.3 | M | `api.py:321-358` | O `@timeout` cria **uma thread por chamada**. Hoje funciona porque o ID HLLAPI é por processo. Se algum dia `MultiThreadConnectionOn()` for usado, cada thread nova consome 1 dos 32 slots → *popup modal* "Exceeded maximum number of clients, reboot for correction". | Thread dedicada e de vida longa. |
| 3.4 | M | `server.py:37-52` | `screen_load` faz *polling* de 1 s (latência média de 0,5 s por espera, 2 min fixos de timeout, sem parâmetro). | *Polling* de 50–100 ms + `WD_Wait`/OIA, timeout configurável. |
| 3.5 | M | `server.py:20`, `169-172` | `sessions` nunca é limpo (vazamento a cada `RumbaClient`); acesso sem lock entre threads. | Ciclo de vida explícito por sessão. |

## 4. Ciclo de vida do terminal

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 4.1 | **A** | `api.py:243-256` | **Requisito do dono:** fechar **todas** as instâncias do perfil é obrigatório — duas instâncias da mesma letra fazem a 2ª exibir o diálogo modal "RUMBA API Service Control — Short Name already in use" e o HLLAPI fica preso à 1ª (validado ao vivo em 2026-10-03). O problema é o **critério**: o regex de título casa qualquer janela do desktop (ex.: uma planilha `BRAZIL_D.xlsx`). | Fechar todas as janelas que sejam **sessões RUMBA** do perfil (título `"<PERFIL> - Pro Client"` em processo do Web Launcher/IE), com `WM_CLOSE`, e tratar o diálogo "Short Name already in use". |
| 4.2 | **A** | `server.py:67`, `125` | `logon_*` sempre fecha o terminal existente antes de abrir outro → derruba sessões em uso por outros scripts/clientes. | Reutilizar a sessão se já estiver conectável; fechar só por pedido explícito. |
| 4.3 | **A** | `api.py:174-230` | Depende de `InternetExplorerMedium` (IE11 aposentado; só a automação COM ainda funciona). Se o link não for encontrado (`api.py:191-194`, sem `else`), o erro aparece 60 s depois como "Timeout aguardando a janela" — mensagem enganosa. | Abrir via `hawlauncher://` (Host Access Web Launcher, já instalado) ou hospedar o ActiveX no próprio processo; erro imediato se o link/perfil não existir. |
| 4.4 | M | `api.py:127` | `base_url` usa `http://`; o servidor responde em `https://` (o `RumbaWebToHost.vbs` e os logs do NMServiceManager usam https). | Usar `https://`. |
| 4.5 | M | `server.py:132` | `logon_rhelp` não verifica o rc do `WD_ConnectPS` nem tenta de novo (o CICS tenta 60×). | Mesmo laço de conexão com diagnóstico do rc. |
| 4.6 | M | `api.py:331-332`, `313-319` | rc de `WD_SendKey` e `WD_Wait` ignorados → teclas não enviadas (rc 4/5: host ocupado/inibido) passam em silêncio. | Mapear rc → exceções tipadas; tratar rc=5 com `RESET` opcional. |
| 4.7 | B | `server.py:22-27`, `54` | A verificação de "E R R O   G R A V E" (5,31) é específica do CICS, mas roda em todo `screen_load` (inclusive RHELP). | *Hooks* de erro por aplicação. |
| 4.8 | B | `server.py:342-351` | Ao encerrar, o servidor faz `WD_DisconnectPS` mas nunca `WD_ResetSystem` (o manual WD_API manda chamá-lo antes de sair). | Incluir `WD_ResetSystem` no *shutdown*. |

## 5. Segurança

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 5.1 | **A** | `server.py:318-327`, `client.py:327-337` | Socket TCP **sem autenticação** em portas fixas. Qualquer processo local, inclusive **de outro usuário** na mesma máquina (VDI/RDP/Citrix), pode ler telas e digitar na sessão CICS logada do usuário. Também pode haver colisão de portas entre usuários. | *Named pipe* por usuário com ACL + `authkey` (`multiprocessing.connection`). |
| 5.2 | M | `server.py:294` | Em erro, o servidor loga a requisição bruta, que pode conter **a senha** (`logon_*`). | Nunca logar `params` de logon; mascarar. |
| 5.3 | B | `client.py:350-358` | Senha trafega em JSON claro (localhost). Aceitável com 5.1 resolvido. | — |

## 6. Operação / observabilidade

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 6.1 | **A** | `client.py:294-299`, `server.py:17` | stdout/stderr do servidor vão para `DEVNULL` e nenhum *handler* de logging é configurado → **zero diagnóstico** quando algo dá errado no lado 32-bit. | Log rotativo em `%LOCALAPPDATA%\rumbapy\logs\`. |
| 6.2 | M | `server.py:306-354` | O servidor nunca termina (sem *idle timeout*; `KeyboardInterrupt` não chega a ele). Fica vivo até logoff/reboot com o terminal aberto. | *Idle timeout* + comando `shutdown`. |
| 6.3 | M | `server.py:311` vs `client.py:271` | O cliente aceita qualquer `terminal_type` (porta 8800), mas o servidor só aceita `D/A/Z` → o servidor morre no `argparse` e o cliente espera 30 s até um "Timeout aguardando o servidor". | Validar no cliente com mensagem clara. |
| 6.4 | B | `client.py:307` | `except:` nu engole qualquer erro (até `KeyboardInterrupt`) durante a espera. | `except OSError`. |
| 6.5 | B | `client.py:327-337` | Um socket por comando → `TIME_WAIT` acumulado; em laços intensos pode esgotar portas efêmeras (`WinError 10048`, que já apareceu: houve *retry* no commit `c57df6d`). | Conexão persistente. |

## 7. Empacotamento / repositório

| # | Sev | Onde | Problema | Correção sugerida |
|---|---|---|---|---|
| 7.1 | B | `src/rumbapy.egg-info/` | Metadado de build (versão 0.1.0, desatualizado) versionado no Git. | Adicionar ao `.gitignore` e remover. |
| 7.2 | B | `pyproject.toml:5` vs `client.py:65` | `requires-python >=3.12`, mas o Python 32-bit precisa ser ≥3.13 (`find_python_32bit`). | Documentar/alinhar. |
| 7.3 | B | `README.md` | Exemplo passa o caminho do Python 32-bit (hoje opcional); não documenta `screen_load`, códigos de erro, nem acentos. | Reescrever com a nova API. |
| 7.4 | B | — | Nenhum teste automatizado; nenhum *lint*/type-check. | Ver plano, Fase 0. |

# Changelog

Todas as mudanças publicadas no `master` (push no `master` = atualização automática para todos os usuários).
Formato: versão, data, e o que muda para quem usa. A versão está em `pyproject.toml` e `src/rumbapy/_version.py`.

## 2.0.0 — 2026-10-04

Merge do `dev` no `master` (tag `v2.0.0`). O `master` anterior (0.1.5) está salvo na branch `legacy/v0.1.5` e na
tag `v0.1.5`; como voltar: [`docs/ROLLBACK.md`](docs/ROLLBACK.md).

Reescrita completa. **Scripts da 0.1.5 continuam funcionando sem alteração** (`RumbaClient`), com as diferenças de
comportamento listadas em "Compatibilidade" — todas para trocar falhas silenciosas por erros claros.

### Antes de atualizar / depois de atualizar

* Nada a fazer para migrar: o próprio script da 0.1.5 instala a v2 na próxima execução (pelo `pip ... git+https` que
  ele já usava). A execução seguinte já roda a v2.
* **Reinicie todos os kernels do Jupyter e scripts antigos que estavam abertos** depois da v2: um processo que ainda
  tem a 0.1.5 na memória recebe "O RumbaPy foi atualizado para a v2. Rode o script de novo." do stub que substitui o
  antigo servidor.
* `pywin32` e `pywinauto` no Python 32-bit não são mais necessários (podem ficar instalados). Nada é instalado no
  Python 32-bit a partir de agora.
* Servidores 0.1.5 que continuarem vivos (portas 8500/8600/8700/8800) são desconectados do terminal na primeira vez
  que a v2 abre ou anexa aquela letra (nunca são encerrados à força; ficam ociosos até o logoff).

### Novidades

* **API nova** (`import rumbapy`): `rumbapy.open("D")`, `rumbapy.attach("D")`, `rumbapy.close("D")` e a `Session`
  com leitura (`screen`, `snapshot(fields=True)`, `read`, `read_field`, `find`), escrita local (`write`, `fill`,
  `type`, `move_to`), teclas (`enter`, `clear`, `pf`, `pa`, `press`, `send_keys`), esperas (`wait_ready`,
  `wait_text`, `wait_any`, `wait_change`, `wait_cursor`), janela (`show`, `minimize`, `window`) e ciclo de vida
  (`release`, `disconnect`, `reconnect`, `close`, `acknowledge`, `with`).
* `Screen`, `Field`, `Cursor`, `OIA` (tela imutável com régua no `print`, `table()` para listas, exibição HTML no
  Jupyter) e `Key` (todas as teclas 3270 por nome; `send_keys("abc<Tab>1<Enter>")`).
* Receitas `t.cics.logon`, `t.cics.start_transaction`, `t.ims.logon`; detector do "E R R O   G R A V E" do CICS em
  todas as esperas.
* Hierarquia de exceções em português (`rumbapy.RumbaError` e dezenas de subclasses), cada uma com dica de "Como resolver",
  código da DLL e `delivery` (`none`/`partial`/`unknown`) em toda falha de entrada.
* `rumbapy.doctor()` e a linha de comando `python -m rumbapy {version | update | doctor | close <L>}` (ajuda e erros
  de uso em português; sem rede, o `doctor` mostra `[FALHA] Update obrigatório` como item do relatório e sai com 1).
* Teclado bloqueado por erro de digitação (`X -f`): `t.press(Key.RESET)` (ou `<Reset>` no início de `send_keys`)
  destrava sem esperar o teclado livre; o RumbaPy nunca manda RESET por conta própria.
* Logs em `%LOCALAPPDATA%\rumbapy\logs\` (cliente, processo auxiliar por letra, update, pip), sem senha e sem texto de
  tela em nível nenhum.
* Vários terminais ao mesmo tempo (`D`, `Z`, `A`), um processo auxiliar por letra.
* Abertura pelo **Host Access Web Launcher** (sem automatizar o Internet Explorer; o IE fica só como reserva quando o
  launcher não existe ou com `RUMBAPY_LAUNCH=ie`); `window="minimized"` sem roubar o foco.

### Mudanças de arquitetura (o que corrige)

* O servidor TCP 32-bit persistente da 0.1.5 deu lugar a um **processo auxiliar 32-bit por letra** que só usa a
  stdlib e roda **a partir dos arquivos do pacote 64-bit** (sem instalar nada no Python 32-bit). Cliente e processo
  auxiliar conferem o `build_id` dos arquivos: um processo auxiliar antigo é trocado sozinho depois de um update.
* Canal local por **named pipe** com DACL do usuário e autenticação mútua HMAC (antes: socket TCP sem autenticação,
  acessível a outros usuários da máquina).
* **Watchdog**: se uma chamada da DLL travar (ela espera sem prazo), o processo auxiliar sai de forma limpa
  (`os._exit`) e outro sobe na próxima operação; leituras são repetidas uma vez, entradas nunca.
* Texto em **cp1252** (acentos funcionam na leitura e na escrita), leituras com o tamanho pedido (NUL vira espaço),
  `WD_ResetSystem` antes de sair, uma única thread de DLL por processo.
* **Conteúdo de campos ocultos (senhas) nunca sai do processo auxiliar**: telas, leituras, exceções e logs veem
  espaços.
* Fechamento só de janelas que são sessões RUMBA do perfil (antes: qualquer janela cujo título casasse um regex),
  sempre com `WM_CLOSE` (nunca `TerminateProcess`); trata o diálogo "Short Name already in use".
* Fechar um terminal derruba a conexão HLLAPI dos outros (comportamento do RUMBA, achado ao vivo): o processo
  auxiliar reconecta sozinho à mesma janela e repete a operação uma vez (só leitura, ou entrada recusada antes de a
  primeira tecla ser aceita).
* `open`/`close` revogam o uso do terminal por outro script (a `Session` dele passa a levantar `SessionReplaced` e
  nunca digita na tela nova).

### Auto-update 2.0 (continua obrigatório)

* Compara o commit instalado com o `refs/heads/master` do GitHub por HTTPS (sem exigir Git; `git ls-remote` e a API
  do GitHub são reservas), uma vez por processo no `import` e de novo no `open` depois de 1 h (interativo) ou 12 h
  (script).
* Instala com `pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/<sha>.zip`
  e levanta `UpdateRequired` ("Rode o script de novo." / "Reinicie o kernel…"): **o script nunca é reexecutado
  sozinho** (antes: a versão nova só valia na próxima execução, e o servidor 32-bit antigo continuava rodando).
* Sem rede, `UpdateCheckFailed` bloqueia (não há modo offline).
* `heal`: se um commit quebrado for publicado, o `import` se repara sozinho quando a correção chegar ao `master`.
* `auto_update=False` só vale com `RUMBAPY_DEV=1` (modo de desenvolvimento, sempre avisado no stderr e no log).
* Empacotamento sem PyPI: backend PEP 517 próprio (`_build/rumbapy_build.py`, só stdlib); `dependencies = []`;
  requer Python ≥ 3.12 nos dois lados.

### Compatibilidade (`RumbaClient` e módulos da 0.1.5): diferenças de comportamento

Assinatura idêntica: `RumbaClient(terminal_type, python32bit_path=None, auto_update=True)`. Toda falha levanta a
exceção v2 original (tipo preservado; todas são `RuntimeError`) com a mensagem prefixada por
`Comando '<ação>' falhou: `; no logon, o resto começa com o texto da 0.1.5 (`SENHA DO CICS ESTÁ EXPIRADA` /
`SENHA DO CICS ESTÁ INCORRETA`).

| Item | 0.1.5 | 2.0.0 |
|---|---|---|
| `RumbaClient(letra)` | aceitava qualquer letra (o servidor morria depois) | letra inválida → `UnknownTerminal` na hora |
| `python32bit_path` | precisava de Python 32-bit ≥ 3.13 com pywin32/pywinauto | ≥ 3.12, nada instalado nele; valor falso (`""`, `None`) = descoberta automática |
| `auto_update=False` | desligava o update | ignorado (WARNING) salvo com `RUMBAPY_DEV=1` |
| `HOST` / `PORT` | servidor TCP | existem, sem uso |
| `connected` | atributo | property: `True` só depois de `logon_*`/`connect`/anexo automático bem-sucedido e enquanto a sessão estiver conectada |
| `logon_cics` | qualquer letra | só terminais CICS (`D`, `Z`; senão `RecipeNotAvailable`); confere que o campo de senha é oculto antes de escrever |
| `logon_rhelp` | qualquer letra | só terminal IMS (`RumbaClient("A")`) |
| `send_key` | rc ignorado; a DLL descartava calada o que viesse depois da 1ª AID | rc ≠ 0 levanta; o trecho após a 1ª AID é ignorado com WARNING "send_key: ignorado o trecho após a 1ª tecla AID (comportamento da v0.1.5)"; espera a resposta do host até 120 s (ou `Session.timeout`, se maior) → `NoHostResponse` |
| teclado bloqueado | a conexão usava AUTORESET (destravava sozinho) | `NORESET`: `KeyboardLocked` com a dica de `send_key('RESET')` (nunca manda RESET sozinho); `send_key('RESET')` destrava o `X -f` sem esperar o teclado livre (só espera o fim do `X SYSTEM`) |
| `copy_ps_to_string` | UTF-8 (acento derrubava) e corte no 1º NUL | cp1252; NUL no meio ainda corta como antes; com o host ocupado (X SYSTEM) espera até 120 s; `length=0` → `""` sem anexar |
| `copy_string_to_ps` | ASCII (acento derrubava); escrevia pela metade | cp1252; `ProtectedField`/`Truncated` levantam **antes** de escrever; `""` não faz nada e devolve `True`; em campo oculto o erro de caractere não cita o caractere |
| `copy_field` | no máximo 80 caracteres | tamanho real do campo; tira só os NUL finais |
| `screen_load` | polling de 1 s; "erro grave" testado em todo perfil | polling de 50–100 ms, prazo 120 s; a condição é testada antes do detector; erro grave só em CICS; `text=""` → `True`; `text_diff=True` com texto vazio → `ParameterError` |
| `close_terminal` | fechava janelas por regex de título (podia fechar uma planilha `BRAZIL_D.xlsx`) | fecha só janelas RUMBA do perfil, de qualquer script; também com a sessão substituída (que é descartada) |
| `disconnect` | — | sem sessão: não faz nada; sessão substituída: descarta e devolve `True` |
| `connect` | devolvia `True` mesmo sem terminal | sem terminal → `TerminalNotOpen`; sessão fechada/substituída → anexa de novo (60 s) |
| qualquer op sem `logon_*`/`connect` | — | anexa sozinha ao terminal aberto (10 s) |
| op numa sessão substituída (outro `open`/`close`) | digitava na tela nova | `SessionReplaced` com a dica "chame connect() deste RumbaClient…" |
| dicas das exceções | — | citam os métodos do `RumbaClient`, não os da API v2 |
| `rumbapy.client.update_python_64bit()` | atualizava e seguia com o código velho | força a checagem, instala e levanta `UpdateRequired`; nunca reexecuta |
| `ensure_python_32bit` / `update_python_32bit` | instalavam pacotes no 32-bit | só validam o Python 32-bit (log INFO) |
| `find_python_32bit()` | ≥ 3.13 | ≥ 3.12; `Python32NotFound` (que é `ValueError`) |
| `python -m rumbapy.server` | servidor com a DLL | stub sem DLL: responde `ping` e pede "rode o script de novo" a qualquer outra ação; sai em 120 s |

### Removido

* `rumbapy.api` (lógica com pywin32/pywinauto/IE COM): importar levanta `ImportError("rumbapy.api foi removido na v2;
  use rumbapy.open(...) ou RumbaClient")`.
* O servidor TCP 32-bit (`rumbapy.server` virou stub), o extra `server32` (pywin32/pywinauto) e a instalação de
  pacotes no Python 32-bit.
* A abertura do terminal por automação do Internet Explorer como caminho padrão (virou reserva).
* `src/rumbapy.egg-info/` do Git.
* `register_profile`, `window="hidden"` e outras ideias da proposta ficaram de fora (ver `docs/ESPECIFICACAO_V2.md`
  K.4).

## 0.1.5 — 2026-08-18

Última versão da linha 0.1: `RumbaClient` (cliente TCP 64-bit) + servidor 32-bit persistente com pywin32/pywinauto;
auto-update por `git ls-remote` + `pip` nos dois Pythons. Problemas conhecidos dessa versão: `docs/KNOWN_ISSUES.md`.

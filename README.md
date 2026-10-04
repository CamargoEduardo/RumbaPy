# RumbaPy

Automação, em Python, dos terminais **3270 (CICS e IMS)** abertos pelo **RUMBA Web-to-Host**: abrir o terminal, fazer
logon, ler e escrever na tela, mandar teclas e esperar a resposta do host — com mensagens de erro em português e sem
nunca mandar ao host algo que você não pediu.

```python
import rumbapy

with rumbapy.open("D") as t:                      # abre o BRAZIL_D (fecha antes as janelas antigas do perfil)
    t.cics.logon("A000000", "minha senha")
    print(t.screen)                               # a tela, com régua de colunas e números de linha
```

> Versão **2.0.0**. Scripts escritos para a 0.1.5 (`RumbaClient`) continuam funcionando sem alteração
> ([Compatibilidade](#compatibilidade-com-a-015-rumbaclient)). O que mudou: [`CHANGELOG.md`](CHANGELOG.md).

## Requisitos

| Item | Detalhe |
|---|---|
| Windows | 10 ou 11 (o RUMBA só existe no Windows) |
| RUMBA | instalado pelo portal do RUMBA da empresa (traz a `ehlapi32.Dll` e o **Host Access Web Launcher**) |
| Python 64-bit ≥ 3.12 | o "seu" Python: é nele que você instala o RumbaPy, o pandas etc. |
| Python 32-bit ≥ 3.12 | só para o processo auxiliar (a DLL do RUMBA só existe em 32-bit). **Nada** é instalado nele |
| Rede | acesso ao GitHub (o RumbaPy confere a versão a cada execução) e ao mainframe (VPN, se for o caso) |

O Python 32-bit é o "Windows installer (32-bit)" do [python.org](https://www.python.org/downloads/windows/)
(3.13 recomendado). Não precisa de `pip install` nele: o RumbaPy roda o processo auxiliar a partir dos arquivos do
pacote instalado no Python 64-bit. Ele é encontrado sozinho, nesta ordem (para no primeiro válido):

1. o argumento `python32=` de `rumbapy.open/attach` (ou `python32bit_path` do `RumbaClient`);
2. a variável de ambiente `RUMBAPY_PYTHON32` (caminho do `python.exe`);
3. o próprio Python, se o seu script já roda em 32-bit;
4. o cache `%LOCALAPPDATA%\rumbapy\runtime.json` (da última descoberta);
5. `py -0p` (o lançador do Python lista os instalados);
6. as pastas padrão: `%LOCALAPPDATA%\Programs\Python\Python3*-32\`, `%LOCALAPPDATA%\Python\pythoncore-3.*-32\`,
   `C:\Program Files (x86)\Python3*\`.

Um caminho dado em 1 ou 2 que não sirva dá erro na hora (`Python32NotFound`, com o motivo), sem procurar nos outros.

## Instalação (1 comando)

No `cmd` (tecla Windows → `cmd` → Enter), com o Python **64-bit**:

```bat
python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip
```

Se o `python` do PATH não for o 64-bit, use o caminho completo (ex.:
`%LOCALAPPDATA%\Programs\Python\Python313\python.exe -m pip ...`). O RumbaPy não tem dependências; pandas/openpyxl só
são necessários se o **seu** script os usar. Confira tudo com:

```bat
python -m rumbapy doctor
```

Quem já usava a 0.1.5 não precisa fazer nada: o próprio script antigo instala a v2 na próxima execução. Depois disso,
**reinicie todos os kernels do Jupyter e scripts que estavam abertos**. Os pacotes `pywin32`/`pywinauto` instalados
no Python 32-bit pela 0.1.5 não são mais usados (podem ficar).

## Exemplo completo: CICS em lote

Padrão seguro: depois de cada tecla AID (Enter, PFn…) confirme a tela e **pare no primeiro erro**.

```python
import re, getpass, pandas as pd, rumbapy

df = pd.read_csv("custos.csv", sep=";", dtype=str).dropna(subset=["PN"])
resultado = []
with rumbapy.open("D", window="minimized") as t:
    t.cics.logon("A000000", getpass.getpass("Senha CICS: "))
    t.cics.start_transaction("SPRE", wait_for=("VOLVO", 1, 2))
    for row in df.itertuples():                                 # coordenadas ilustrativas
        try:
            t.fill(3, 14, row.PN).enter()
            t.wait_text(row.PN, 3, 14)                          # a tela é do PN certo?
            t.fill(14, 45, row.CUSTO, align="right").enter()
            tela = t.wait_any({"ok": ("ATUALIZADO", 23, 7), "erro": re.compile(r"INVALID|ERRO")})
            resultado.append({"PN": row.PN, "STATUS": tela.match, "MSG": tela.get(23, 7, 74).strip()})
        except rumbapy.RumbaError as e:
            resultado.append({"PN": row.PN, "STATUS": f"Falha: {e.message}"})
            break                                               # NUNCA siga para o próximo PN numa tela não confirmada
pd.DataFrame(resultado).to_excel("resultado.xlsx", index=False)
```

Coordenadas são sempre `(linha, coluna)`, começando em 1. Ao sair do `with` o RumbaPy só **devolve** o terminal (a
janela continua aberta e logada); use `rumbapy.open("D", close_on_exit=True)` para fechá-la no fim.

### Outros exemplos

**IMS** (perfil `A`, tela 32×80):

```python
with rumbapy.open("A") as t:
    t.ims.logon("A000000", getpass.getpass("Senha IMS: "))     # padrão: app="RHELP"
```

**Lista paginada** (lê as linhas 5 a 20 e avança com PF8 até a tela parar de mudar):

```python
colunas = {"PN": (2, 10), "DESCRICAO": (13, 30), "CUSTO": (60, 12)}   # nome: (coluna, tamanho)
linhas = []
tela = t.screen
while True:
    linhas += tela.table(5, 20, colunas)
    t.pf(8)
    try:
        tela = t.wait_change(since=tela, timeout=10)
    except rumbapy.WaitTimeout:
        break                                                   # a tela não mudou: fim da lista (ou teste a mensagem do seu sistema)
df = pd.DataFrame(linhas)
```

**Jupyter / exploração** (anexa ao terminal que já está aberto, sem fechar nada):

```python
t = rumbapy.attach("D")
t.screen                                        # mostra a tela formatada na célula
t.snapshot(fields=True).input_fields            # campos onde se pode digitar
t.read(1, 2, 10)
t.release()                                     # IMPORTANTE ao terminar: devolve o terminal para outros scripts
```

Enquanto uma `Session` está conectada, outro script não consegue usar o mesmo terminal (`TerminalInUse`). No Jupyter,
chame `t.release()` (ou reinicie o kernel) quando terminar.

## A API em uma página

```python
t = rumbapy.open("D", reuse=False, window="normal", timeout=90, close_on_exit=False, python32=None)
t = rumbapy.attach("D", timeout=10)             # não destrutivo: nunca abre nem fecha janelas
rumbapy.close("D")                              # fecha TODAS as janelas do perfil; devolve quantas

# leitura (local: nada vai ao host)
t.screen / t.snapshot(fields=True)              # Screen: .text .lines .cursor .oia .fields .get() .find() .table()
t.read(linha, col, tamanho); t.read_field(linha, col); t.find("texto")

# escrita local (o host só recebe na próxima tecla AID)
t.write(linha, col, "abc"); t.fill(linha, col, "abc", align="left"); t.type("abc"); t.move_to(linha, col)

# teclas (as AIDs transmitem ao host)
t.enter(); t.clear(); t.pf(3); t.pa(1); t.press(rumbapy.Key.TAB); t.send_keys("abc<Tab>123<Enter>")

# esperas (todas devolvem a Screen que satisfez a condição)
t.wait_ready(); t.wait_text("MENU", 1, 2); t.wait_text("AGUARDE", gone=True)
t.wait_any({"ok": ("OK", 23, 2), "erro": re.compile("ERRO")}); t.wait_change(since=tela); t.wait_cursor(22, 56)

# janela (nunca rouba o foco)
t.show(); t.minimize(); t.window

# ciclo de vida
t.release(); t.disconnect(); t.reconnect(); t.close(); t.acknowledge()
```

Terminais: `"D"` = `BRAZIL_D` (CICS), `"Z"` = `BRAZIL_Z` (CICS), `"A"` = `IMS_A` (IMS); vale a letra ou o nome, sem
diferenciar maiúsculas. Teclas: `Key.ENTER`, `Key.PF1`…`Key.PF24`, `Key.PA1`…, `Key.CLEAR`, `Key.TAB`,
`Key.ERASE_EOF`, `Key.RESET`, `Key.PAGE_DOWN`…; em `send_keys`, `<Nome>` (ex.: `<Tab>`, `<PF3>`) e `<<` = `<`.
Detalhes: `help(rumbapy.open)`, `help(t)` (a `Session`), `help(rumbapy.Screen)`, `help(rumbapy.Key)`.

## Regras em que você pode confiar

1. **Nada vai ao host sem pedido explícito.** Só `press`/`enter`/`clear`/`pf`/`pa`/`send_keys` com uma tecla AID
   transmitem. `write`/`fill`/`type` só mudam a tela local (o host recebe na próxima AID). `open`, `attach`, `close`,
   `release`, `disconnect`, `screen`, `read*` e `wait*` são locais.
2. **Nunca há reenvio automático de entrada.** Só leituras são repetidas (uma vez) depois de reciclar o processo
   auxiliar; numa queda da conexão HLLAPI, também uma digitação que o RUMBA recusou antes de aceitar qualquer tecla.
   Uma digitação que pode ter chegado ao RUMBA nunca é repetida.
3. Antes de toda tecla espera-se o teclado livre; antes de toda AID, a tela estável. A AID vai **sozinha** no fim, e a
   espera depois dela compara com a tela tirada depois da digitação.
4. Toda exceção de entrada diz `e.delivery`: `"none"` (nada chegou ao RUMBA), `"partial"` (parte pode ter sido
   aceita) ou `"unknown"` (não dá para saber). Com `partial`/`unknown` a `Session` fica `uncertain` e recusa nova
   entrada (`SessionUncertain`) até uma espera de conteúdo dar certo (`wait_text`/`wait_any`/`wait_cursor`/
   `wait_change`) ou você chamar `t.acknowledge()`. **Nunca repita uma AID dentro de `except`: reancore pela tela.**
5. Texto que não cabe no campo é recusado **antes** de copiar (`Truncated`), e campo protegido também
   (`ProtectedField`): nada é escrito pela metade.
6. O conteúdo de campos ocultos (senha) **nunca** sai do processo auxiliar: `Screen`, `read`, exceções, a exibição no
   Jupyter e os logs veem espaços. Texto escrito ou digitado nunca vai a log, em nível nenhum.
7. **Senha só em campo oculto.** `fill(..., secret=True)` (e `send_keys(..., secret=True)`) esconde o valor de logs e
   mensagens, mas num campo **visível** ele só fica mascarado na leitura até o host responder à próxima AID: depois
   disso a tela mostra o que o host mandou. As receitas `t.cics.logon`/`t.ims.logon` conferem que o campo de senha é
   oculto antes de escrever.
8. Leituras têm exatamente o tamanho pedido (NUL vira espaço); acentos são corretos (cp1252).
9. Ctrl+C interrompe esperas na hora; durante uma digitação, a sessão fica `uncertain`.
10. `open` e `close` fecham **todas** as janelas daquele perfil, de qualquer script (o outro script passa a receber
    `SessionReplaced` e nunca digita na tela nova); `attach` nunca fecha nada. Só janelas do RUMBA são tocadas (uma
    planilha `BRAZIL_D.xlsx` nunca é fechada).
11. Nenhuma ação da biblioteca põe o terminal em primeiro plano nem ativa a janela.

## Vários terminais ao mesmo tempo

Cada letra tem o seu processo auxiliar 32-bit, então `D`, `Z` e `A` podem ser usados juntos (no mesmo script ou em
scripts diferentes). Cada letra, porém, é usada por um script de cada vez. Cada perfil abre no seu próprio processo
do Web Launcher.

Atenção a um comportamento do RUMBA: **fechar um terminal derruba a conexão HLLAPI dos outros** (a janela do outro
continua aberta e logada). O RumbaPy trata isso sozinho: a próxima operação do outro terminal reconecta à **mesma**
janela e repete a operação uma vez — só se for leitura, ou se for uma digitação que o RUMBA recusou antes de aceitar
qualquer tecla. Se a janela tiver mudado, você recebe `SessionReplaced`; se tiver sumido, `NotConnected`.

## Erros: o que significam e o que fazer

Toda falha é uma subclasse de `rumbapy.RumbaError` (que é `RuntimeError`); `str(e)` traz a mensagem, a dica
("Como resolver") e o código da DLL quando houver. Atributos: `e.message`, `e.hint`, `e.rc`, `e.function`,
`e.terminal`, `e.delivery`, `e.screen` (só em esperas/receitas, e nunca com conteúdo oculto).

| Exceção | Quando | O que fazer |
|---|---|---|
| `UpdateRequired` | o RumbaPy acabou de se atualizar | rode o script de novo (Jupyter: reinicie o kernel) |
| `UpdateCheckFailed` | sem acesso ao GitHub | conecte-se à rede/VPN e rode de novo; para fechar terminais agora, use o X da janela |
| `UpdateFailed` | o `pip` falhou ao atualizar | veja o log citado e rode o comando de recuperação abaixo |
| `Python32NotFound` | não há Python 32-bit ≥ 3.12 válido | instale o "Windows installer (32-bit)" do python.org ou informe `python32=` |
| `RumbaNotInstalled` | DLL ou Web Launcher ausente | instale o RUMBA pelo portal |
| `UnknownTerminal` | letra/perfil inválido | use `D`, `Z` ou `A` |
| `TerminalNotOpen` | `attach` sem terminal aberto | `rumbapy.open("D")` |
| `MultipleInstances` | 2+ janelas do perfil no `attach` (ou uma que não fecha no `open`) | `rumbapy.open("D")` fecha todas; aba do Edge no modo IE: feche-a |
| `TerminalInUse` | outro script está usando a letra | espere-o, ou `rumbapy.open`/`rumbapy.close` (revogam o uso do outro) |
| `LaunchTimeout` / `LaunchFailed` | o terminal não abriu a tempo / o launcher falhou | confira a VPN/rede; a mensagem diz em que fase parou |
| `CloseFailed` / `InstanceNotClosable` | uma janela não fechou | feche-a pelo X (ou a aba do Edge que segura a letra) |
| `SessionReplaced` | a janela desta `Session` foi fechada/reaberta (por você ou outro script) | use a `Session` nova (`rumbapy.attach`) |
| `SessionUncertain` | a última entrada pode ter chegado ao host | confirme a tela com `wait_text`/`wait_any` ou `t.acknowledge()` |
| `NotConnected` / `SessionStopped` | a conexão HLLAPI ou a janela do emulador sumiu | `t.reconnect()` ou `rumbapy.open` |
| `HostConnectionLost` | a conexão com o mainframe caiu | `rumbapy.open("D")` de novo |
| `KeyboardLocked` / `HostBusy` | teclado bloqueado / host ocupado (`X SYSTEM`) | olhe a tela antes de repetir; `X -f` → `t.press(Key.RESET)` |
| `Truncated` / `ProtectedField` / `InvalidPosition` / `FieldNotFound` | escrita fora do lugar | confira as coordenadas (`print(t.screen)`) |
| `EncodingError` | caractere que o terminal não tem | troque o caractere (ou `errors="replace"`) |
| `UndefinedKey` / `ParameterError` | tecla ou argumento inválido | a mensagem sugere o nome certo |
| `WaitTimeout` / `NoHostResponse` | a tela esperada não veio / o host não respondeu à AID | `print(e.screen)`; **não** repita a tecla, reancore pela tela |
| `UnexpectedScreen` / `LogonFailed` / `HostError` | a receita achou outra tela / erro grave do CICS | `rumbapy.open` começa de uma tela limpa |
| `PasswordExpired` / `InvalidPassword` | senha do CICS expirada/errada | troque a senha manualmente no terminal |
| `RecipeNotAvailable` | `t.cics` num IMS (ou o contrário) | use a receita do tipo do terminal |
| `WorkerHung` / `WorkerCrashed` / `WorkerStartFailed` | o RUMBA travou / o processo auxiliar parou ou não subiu | rode de novo; `python -m rumbapy doctor` ajuda a diagnosticar |
| `HllapiSystemError` | erro interno do RUMBA (rc 9) | confira a tela antes de repetir; se persistir, feche e reabra o terminal (`rumbapy.open`) |
| `UnsupportedPlatform` | fora do Windows | o RumbaPy só roda no Windows com o RUMBA instalado |
| `ClientTableFull` | o RUMBA esgotou os 32 clientes HLLAPI | feche janelas do RUMBA e scripts; se persistir, faça logoff |
| `ChannelHijacked` / `ProtocolError` | o canal com o processo auxiliar não é confiável | feche os scripts e rode de novo; avise o responsável se repetir |

Grupos úteis no `except`: `TerminalError`, `HllapiError`, `WorkerError`, `UpdateError`, `SetupError`, `RecipeError`,
`WaitTimeout`.

## Auto-update obrigatório

Você sempre roda a versão publicada no GitHub — é um requisito, não dá para desligar:

* a cada `import rumbapy` (e de novo depois de 1 h no Jupyter/REPL ou 12 h num script, ao abrir um terminal) o
  RumbaPy confere o último commit publicado. Se houver versão nova, instala com `pip` e levanta `UpdateRequired`:
  **rode de novo** (no Jupyter, reinicie o kernel). O script nunca é reexecutado sozinho;
* **sem rede não há atalho**: sem alcançar o GitHub, `UpdateCheckFailed` bloqueia o uso. Conecte-se e rode de novo.
  Para fechar um terminal aberto nesse meio-tempo, use o X da janela `<PERFIL> - Pro Client`;
* se uma versão publicada vier quebrada, o próprio `import` tenta se reparar quando a correção for publicada;
* o processo auxiliar 32-bit roda sempre os mesmos arquivos do pacote 64-bit (um processo antigo é trocado sozinho).

Se algo der errado com a instalação, o **comando de recuperação** é o mesmo da instalação:

```bat
python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip
```

## Linha de comando

```bat
python -m rumbapy version        :: versão, commit instalado, modo de instalação, última checagem (não usa a rede)
python -m rumbapy update         :: força a checagem e instala a versão publicada
python -m rumbapy doctor         :: diagnóstico: RUMBA, Web Launcher, Python 32-bit, update, processos auxiliares, logs
python -m rumbapy close D        :: fecha todas as janelas do terminal D
```

Saída: 0 = ok, 1 = erro (mensagem em português), 2 = uso inválido, 3 = acabou de atualizar (rode de novo), 130 = Ctrl+C.
Sem rede, o `doctor` mostra `[FALHA] Update obrigatório` e roda o resto do diagnóstico.

## Ajustes por variável de ambiente

| Variável | Para quê |
|---|---|
| `RUMBAPY_PYTHON32` | caminho do `python.exe` 32-bit |
| `RUMBAPY_OPEN_TIMEOUT` | prazo do `open` (padrão 90 s) |
| `RUMBAPY_TIMEOUT` | prazo padrão das esperas (padrão 30 s; também `t.timeout = 60`) |
| `RUMBAPY_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` dos logs (diagnóstico) |
| `RUMBAPY_QUIET=1` | silencia os avisos curtos no stderr ("RumbaPy: atualizando…", "fechando 1 janela…") |
| `RUMBAPY_LAUNCH=ie` | força a abertura pelo Internet Explorer em vez do Web Launcher (sem a variável, o IE só é usado quando o launcher não está instalado) |

Logs ficam em `%LOCALAPPDATA%\rumbapy\logs\` (`client-AAAAMMDD.log`, `worker-*.log`, `update.log`); nunca contêm
senhas nem texto de tela. `RUMBAPY_DEV=1` (desliga o auto-update) é só para quem desenvolve o RumbaPy.

## Compatibilidade com a 0.1.5 (`RumbaClient`)

```python
from rumbapy import RumbaClient                  # ou: from rumbapy.client import RumbaClient

cics = RumbaClient("D")                          # o 2º argumento (caminho do Python 32-bit) é opcional
cics.logon_cics("A000000", senha)
cics.copy_string_to_ps(1, 2, "SPRE")
cics.send_key("ENTER")
cics.screen_load(1, 2, "VOLVO")
print(cics.copy_ps_to_string(1, 1, 80))
```

Os mesmos métodos (`logon_cics`, `logon_rhelp`, `send_key`, `copy_ps_to_string`, `copy_string_to_ps`, `copy_field`,
`screen_load`, `close_terminal`, `disconnect`, `connect`) e retornos. Diferenças, todas para não falhar em silêncio
(lista completa no [`CHANGELOG.md`](CHANGELOG.md)): erros que antes passavam calados agora levantam exceção (com o
prefixo `Comando '<ação>' falhou:` da 0.1.5); `send_key` ignora tudo depois da 1ª tecla AID (com aviso no log);
`logon_rhelp` só vale em `RumbaClient("A")`; `connect()` sem terminal aberto levanta `TerminalNotOpen`;
`auto_update=False` é ignorado; acentos agora funcionam; `copy_field` devolve o campo inteiro.

## Problemas conhecidos

* A primeira abertura de um terminal leva de ~10 a ~30 s (o RUMBA registra a sessão e conecta ao host).
* Duas janelas do mesmo perfil abertas à mão: o RUMBA mostra "Short Name already in use" e às vezes a 2ª janela cai
  ao ser fechada (defeito do RUMBA; o `open` fecha as duas e abre uma nova).
* Abas do Edge no modo IE com um perfil do RUMBA podem segurar a letra: feche-as.
* O terminal só é automatizável se for aberto pelo RumbaPy (ou por um perfil com HLLAPI do portal); o atalho comum do
  RUMBA abre perfis **sem** HLLAPI.
* Um mesmo terminal só pode ser usado por um script por vez; `open`/`close` de outro script interrompem o seu
  (`SessionReplaced`).
* O logon IMS (`t.ims.logon` / `logon_rhelp`) segue as telas da 0.1.5 e ainda não foi validado ao vivo na v2.
* Sem acesso ao GitHub o RumbaPy não roda (auto-update obrigatório, sem modo offline).
* Lista completa e status: [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

## Para quem desenvolve

Leia [`INDEX.md`](INDEX.md) e [`CLAUDE.md`](CLAUDE.md) (regras do repositório). Testes nunca tocam o RUMBA real: veja
`CLAUDE.md` → "Ambiente de desenvolvimento". O repositório é público: nada de dados internos em arquivos versionados.

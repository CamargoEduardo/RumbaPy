# Referência técnica: `ehlapi32.Dll` do RUMBA (EHLLAPI / funções `WD_*`)

> Fonte primária: **engenharia reversa** da DLL instalada (desmontagem x86 com `tools/research/rdis.py`),
> cruzada com a especificação IBM EHLLAPI e com os parâmetros dos perfis do portal.
> Versão analisada: `ehlapi32.Dll` **10.2.1710.0** (102.952 bytes, "RUMBA EHLLAPI DLL",
> "Micro Focus Rumba+ Desktop 10.2 GA DECO 23861"), em
> `C:\Program Files (x86)\Micro Focus\RUMBA\System\`.
> Legenda: **[RE]** confirmado no binário · **[.NET]** confirmado nos P/Invoke/constantes oficiais do
> `RDEEHLLAPI.dll` · **[TLB]** type library do fornecedor · **[WDAPI]** manual "WD_API Language Reference"
> (Attachmate, 2004 — reimplementação documentada da API da Wall Data/RUMBA) · **[IBM]** semântica do padrão
> EHLLAPI · **[a validar]** hipótese ainda não testada com sessão real. Fontes completas no §12.

---

## 1. Modelo mental em uma página

```
 Seu processo (32-bit)                                Processo do emulador (iexplore / launcher, 32-bit)
 ┌───────────────────────────┐                        ┌─────────────────────────────────────────────┐
 │ WD_CopyPSToString(h,...)  │                        │ WDApiSvc.ocx ("ObjectX HLLAPI Control")      │
 │   └ dispatch(h, 8, ...)   │                        │   chamou APIRegisterApp(hwnd,'D',fmt) na     │
 │      1 mutex "PID<pid>"   │                        │   carga → publica a sessão D na .SHARDAT     │
 │      2 slot do cliente    │    .SHARDAT (seção     │                                             │
 │      3 HLLAPIMem<id>  ◄───┼──── compartilhada da ───┼──► lê a requisição na memória nomeada       │
 │      4 Event Global\xyz   │    ehlapi32.Dll)        │   executa no WdMfDsp.ocx (tela 3270)         │
 │      5 PostMessage(hwnd,  │ ─── "WDHLLAPI SEND" ──► │   escreve rc/dados, SetEvent(Global\xyz)     │
 │         "WDHLLAPI SEND")  │                        │                                             │
 │      6 Wait(evento, ∞) ◄──┼────────────────────────┤                                             │
 └───────────────────────────┘                        └─────────────────────────────────────────────┘
```
Consequências:
* Só funciona com **a mesma DLL física** carregada nos dois lados (a seção compartilhada é por arquivo
  de imagem) e em **32-bit**.
* Só existem sessões que o emulador **publicou** (perfil com HLLAPI; ver `RUMBA_AMBIENTE.md` §2).
* A chamada é **síncrona e sem prazo** do lado do cliente.

---

## 2. As funções `WD_*` são uma fachada sobre o EHLLAPI numerado  **[RE]**

Todas são `__stdcall` e chamam um despachante interno (`0x100034d0`):

```c
WORD dispatch(HWND hApp, WORD func, WORD *pOut, WORD psPos, WORD length, char *data);
```
* `hApp` (1º parâmetro de todas as `WD_*`): identificador da aplicação chamadora. **Não** é a identidade
  da conexão (ver §4). É tratado como `HWND` em `WD_Pause` (`EnableWindow(h, FALSE/TRUE)`) e precisa
  ser ≠ 0 em `WD_ConnectWindowServices`/`WD_QueryWindowCoordinates`/`WD_WindowStatus`. O projeto passa
  `1` ou o HWND do IE — ambos funcionam.
* `psPos`, `length` são **truncados para 16 bits** (`movzx word`).
* O retorno é o **código de retorno EHLLAPI** (WORD). `*pOut` recebe o valor de saída (posição/tamanho).
* `func` é o número de função do EHLLAPI IBM (1=Connect, 8=Copy PS to String …) ou extensões RUMBA (≥1000).

---

## 3. Catálogo completo das exportações (58)  **[RE]** + **[.NET]**

Assinaturas deduzidas da desmontagem (tipos C; `h` = HWND/identificador da aplicação) e **confirmadas
pelos P/Invoke oficiais** da classe `MicroFocus.RDE.Framework.EHLLAPI` (`RDEEHLLAPI.dll`), que declara
por exemplo `short WD_CopyPSToString(IntPtr hWnd, short position, StringBuilder data, short length)`,
`IntPtr WD_GetSessionHWND(IntPtr hWnd)` e `short WD_Convert(IntPtr hWnd, short type, ref Point point, string data)`.
Em ctypes: `h` = `c_void_p`, `WORD` = `c_ushort`/`c_short`, retorno `c_short` (exceto `WD_GetSessionHWND` → `c_void_p`).
Atenção: o wrapper .NET também declara contra a `ehlapi32.Dll` funções que ela **não exporta**:
`WD_StartCloseIntercept`/`WD_QueryCloseIntercept`/`WD_UnhookBlockingHook` (existem só na `WHLLAPI.dll`) e
`APIGetNextAvailableShortName`/`APIRegisterSession`/`APIRemoveSession` (não existem em nenhuma das duas, na 10.2.1710).

| Função (ordinal) | Assinatura | Fn EHLLAPI | Notas |
|---|---|---|---|
| `WD_ConnectPS` (101) | `(h, char *shortName)` | 1 | 1ª letra vira maiúscula. Derruba a conexão anterior do cliente [WDAPI]. rc 4/5 = conectou mas ocupado/travado (sessão ainda subindo → repetir `Wait` até 0) [WDAPI]. |
| `WD_DisconnectPS` (102) | `(h)` | 2 | |
| `WD_SendKey` (103) | `(h, char *keys)` | 3 | **rc=2 se `strlen ≥ 256`** [RE]; o manual recomenda **≤ 128** por chamada [WDAPI]. Texto + mnemônicos `@x`. **Tudo após a 1ª tecla AID é ignorado** [WDAPI]. |
| `WD_Wait` (104) | `(h)` | 4 | Espera X CLOCK/X SYSTEM sair (TWAIT = 60 s). **Não indica "host pronto"** — use texto/cursor [WDAPI]. |
| `WD_CopyPS` (105) | `(h, char *buf, <ignorado>)` | 5 | Tamanho **automático** = linhas×colunas (×2 com EAB); 3º arg é sobrescrito. |
| `WD_SearchPS` (106) | `(h, WORD *pFoundPos, WORD startPos, char *text)` | 6 | Respeita `SRCHALL/SRCHFROM/SRCHFRWD/SRCHBKWD`. |
| `WD_QueryCursorLocation` (107) | `(h, WORD *pPos)` | 7 | |
| `WD_CopyPSToString` (108) | `(h, WORD pos, char *buf, WORD len)` | 8 | `buf` precisa de `len` bytes (sem NUL garantido). |
| `WD_SetSessionParameters` (109) | `(h, DWORD bitmask)` | 9 | Monta a string de opções a partir da tabela §6. |
| `WD_QuerySessions` (110) | `(h, WORD *pCount, char *buf)` | 10 | Entrada: `*pCount` = máximo; saída: quantidade. 16 B/entrada (12 B no formato "standard"). |
| `WD_Reserve` (111) / `WD_Release` (112) | `(h)` | 11 / 12 | Bloqueia/libera o teclado do usuário. |
| `WD_CopyOIA` (113) | `(h, char *buf, WORD len)` | 13 | OIA = 103 B (formato IBM). |
| `WD_QueryFieldAttribute` (114) | `(h, WORD *pAttr, WORD pos)` | 14 | |
| `WD_CopyStringToPS` (115) | `(h, WORD pos, char *buf, WORD len)` | 15 | **rc=2 se `len == 0`**. |
| `WD_Pause` (118) | `(h, WORD halfSeconds)` | 18 | Desabilita a janela `h` durante a pausa. |
| `WD_QuerySystem` (120) | `(h, char *buf[36])` | 20 | Ex.: `"410090422000U00000…"` (versão 4, nível 10). |
| `WD_ResetSystem` (121) | `(h)` | 21 | Volta parâmetros ao padrão e desconecta. |
| `WD_QuerySessionStatus` (122) | `(h, char *buf[20])` | 22 | `buf[0]` = letra (entrada). Layout §7. |
| `WD_StartHostNotification` (123) | `(h, char *data)` | 23 | Opção `'A'` (assíncrona) usa a mensagem `"WdAsnUpd"`. |
| `WD_QueryHostUpdate` (124) | `(h, char *data)` | 24 | |
| `WD_StopHostNotification` (125) | `(h, char *data)` | 25 | |
| `WD_SearchField` (130) | `(h, WORD *pPos, WORD pos, char *text)` | 30 | |
| `WD_FindFieldPosition` (131) | `(h, WORD *pPos, WORD pos, char *code2)` | 31 | `code2` ex.: `"NU"`, `"PU"`, `"T "`. |
| `WD_FindFieldLength` (132) | `(h, WORD *pLen, WORD pos, char *code2)` | 32 | |
| `WD_CopyStringToField` (133) | `(h, WORD pos, char *text)` | 33 | Comprimento = `strlen(text)`. |
| `WD_CopyFieldToString` (134) | `(h, WORD pos, char *buf, WORD len)` | 34 | |
| `WD_DisplayCursor` (137) / `WD_DisplayPS` (138) / `WD_DeletePS` (139) | `(h, …)` | 37/38/39 | Extensões RUMBA. |
| `WD_SetCursor` (140) | `(h, WORD pos)` | 40 | |
| `WD_StartKSIntercept` (150) / `WD_GetKey` (151) / `WD_PostInterceptStatus` (152) / `WD_StopKSIntercept` (153) | `(h, char*)` | 50–53 | Interceptação de teclado (12 B / 8 B). |
| `WD_SendFile` (190) / `WD_ReceiveFile` (191) | `(h, char *cmd)` | 90 / 91 | IND$FILE. |
| `WD_Convert` (199) | `(h, BYTE opt, struct*, char *shortName)` | 99 | Posição ↔ linha/coluna. |
| `WD_ChangeWindowName` (202) | `(h, char *shortName, BYTE opt, char *name≤63)` | 106 | |
| `WD_ConnectWindowServices` (203) / `WD_DisconnectWindowServices` (204) | `(h≠0, char *shortName)` | 101 / 102 | |
| `WD_QueryWindowCoordinates` (205) | `(h≠0, char *buf[20])` | 103 | |
| `WD_WindowStatus` (206) | `(h≠0, char *buf)` | 104 | `buf[4]`: 1=set, 2=query (28 B), 3=query estendido (24 B). |
| `WD_RunProfile` (1000) | `(char *profile, int show)` | — | `http://…` → `ehlhttp!ExecuteHttpProfile` (abre navegador padrão `-new url`); arquivo local → lança o emulador (`/nosplash`) e espera ≤20 s. |
| `WD_ShowSession` (1001) | `(h, int nCmdShow)` | 1001 | `0..9` (`SW_HIDE`…`SW_RESTORE`), senão rc=2. |
| `WD_GetSessionHWND` (1002) | `(h) → HWND` | 1002 | Devolve o HWND (não um rc). |
| `WD_AdviseHostUpdate` (1003) | `(h, DWORD a, DWORD b)` | 1003 | Notificação de atualização de tela por mensagem **[a validar]**. |
| `WD_RunMacro` (1004) | `(h, char *macro)` | 1004 | |
| `WD_SetSessionParamEx` (1005) | `(h, char *params)` | 9 | String livre (ex.: `"TWAIT NORESET"`). |
| `WD_QueryBlockLocation` (1008) | `(h, char *buf[8])` | 1008 | Bloco selecionado com o mouse. |
| `WD_CopyBlockToString` (1009) | `(h, WORD startPos, WORD endPos, char *buf)` | 1009 | Retângulo; `(0,0)` = seleção atual. |
| `MultiThreadConnectionOn` (3000) | `() → WORD` | — | 0 ok; **9** se o cliente já estiver registrado. |
| `SetStandAlone` (6) | `(DWORD)` | — | Flag compartilhado. |
| `APIRegisterApp` (1) / `APIRemoveApp` (2) | `(HWND, char letra, DWORD fmt)` | — | **Chamadas pelo emulador**, não pelo cliente. |
| `hllapi` (100) | `(WORD *fn, char *data, WORD *len, WORD *rc)` | todas | Entrada IBM clássica. |
| `hllc16` (2000) | — | — | Compatibilidade 16-bit. |
| `HLLAPIWndProc` (200) | — | — | WndProc interna. |

---

## 4. Identidade do cliente, threads e limites  **[RE]**

```c
clientId = MAKELONG(LOWORD(GetCurrentProcessId()),
                    multiThreadMode ? LOWORD(GetCurrentThreadId()) : 1);
```
* **Padrão: uma conexão HLLAPI por PROCESSO.** Todas as threads e todos os objetos (`RumbaAPI`)
  do processo compartilham a mesma conexão/PS. O `hApp` não separa nada.
* `MultiThreadConnectionOn()` → **uma conexão por THREAD** (permite várias sessões no mesmo processo,
  uma por thread). Precisa ser chamado **antes** de qualquer uso (senão retorna 9).
* Tabela local de clientes: **32 slots por processo** (0x14 B cada), alocados sob demanda e
  indexados por `clientId`. No modo multithread **cada thread nova gasta um slot**.
* Tabela compartilhada (`.SHARDAT`): **32 clientes no sistema inteiro**.
* Esgotou → **`MessageBox` modal** *"HLLAPI ERROR — Exceeded maximum number of clients, reboot for
  correction."* (trava a automação até alguém clicar).
* O PID é truncado para 16 bits (colisão teórica entre PIDs que diferem por 65536).
* Serialização: toda chamada faz `CreateMutex("PID<pid>")` + `WaitForSingleObject(mutex, 1000)` e
  **ignora o timeout** — depois de 1 s a chamada segue sem exclusão mútua.
* `DllMain(DLL_PROCESS_ATTACH)`: lê a variável de ambiente **`DISPLAY_PROCESS_ID`**; o primeiro
  processo do sistema registra as *window messages* e zera as tabelas.
* `DllMain(DLL_PROCESS_DETACH)`: libera as entradas do processo na tabela compartilhada. **Não roda
  em `TerminateProcess`** (`taskkill /F`, `Popen.kill()`), o que vaza entradas → popup acima.
  Saída normal (`sys.exit`, `os._exit` → `ExitProcess`) roda o *detach*.

**Regras práticas:** um processo por sessão; uma thread de DLL de vida longa; nunca matar o processo
à força; nunca criar thread por chamada.

---

## 5. Protocolo IPC cliente ↔ emulador  **[RE]**

| Elemento | Valor |
|---|---|
| Mensagens registradas | `RegisterWindowMessageA("WDHLLAPI SEND")`, `("WDHLLAPI TERMINATE")`; assíncronas `"WdAsnUpd"` (host update) e `"WdClsInt"` (close intercept) |
| Memória da requisição | `CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, len+0x154, "HLLAPIMem%lX" % clientId)`; tamanho padrão `0x800+0x154`, cresce se `len ≥` atual |
| Cabeçalho | `+0x00 func` · `+0x04 rc` (pré-carregado com 9) · `+0x08 saída` (→ `*pOut`) · `+0x0C posição` · `+0x10 tamanho` · `+0x14 nome do evento` (`"Global\\"` + 0x38 chars aleatórios) · `+0x54 dados` |
| Envio | `CreateEventA(DACL nula, manual)` → `PostMessageA(hwndSessão, WM_WDHLLAPI_SEND, clientId, GetActiveWindow())` → **`WaitForSingleObject(evento, INFINITE)`** (se o evento não puder ser criado: `SendMessageA` síncrono) |
| Tabela de sessões (`.SHARDAT @0x100174e8`) | 27 entradas × 0x18 B indexadas por `letra-'A'`: `+4` HWND da janela de API do emulador, `+0x14` PID do emulador, flags em `+0/+1`; validade por `IsWindow()` |
| Formato dos dados | Flag global definido pelo emulador em `APIRegisterApp(…, fmt)` (perfil `HllapiStandard`): `fmt≠0` = formatos 16-bit/"standard" (QuerySessions 12 B; byte de opção no offset 1), `fmt=0` = formatos 32-bit (16 B; opção no offset 4). Os perfis da Volvo usam `HllapiStandard=false` → **32-bit**. |

O lado servidor (`WDApiSvc.ocx`) usa os mesmos nomes (`HLLAPIMem%lX`, `WDHLLAPI SEND`) e eventos
nomeados `WD_PAUSE%lX` e `WD_WAITHOSTLOCK%lX`. A `WHLLAPI.dll` (WinHLLAPI) usa a mesma família de
protocolo, mas com esperas **finitas** (N×500 ms) e, no `Wait`, **TWAIT = 60.000 ms** e
**LWAIT = 300.000 ms**.

---

## 6. Parâmetros de sessão (`WD_SetSessionParameters` / `WD_SetSessionParamEx`)  **[RE]+[IBM]**

Tabela interna (`0x1000eb10`) — bit → palavra enviada:

| Bit | Opção | Significado [IBM] |
|---|---|---|
| `0x00000004` / `0x00000008` | `ATTRB` / `NOATTRB` | Copiar bytes de atributo de campo como estão / convertê-los em espaço (padrão `NOATTRB`) |
| `0x00000080` / `0x00000100` | `EAB` / `NOEAB` | Incluir atributos estendidos (dobra o tamanho do buffer) / não (padrão) |
| `0x00040000` / `0x00080000` | `XLATE` / `NOXLATE` | Traduzir EAB para formato CGA / não |
| `0x00000001` / `0x00000002` | `NEWRET` / `OLDRET` | Convenção de códigos de retorno |
| `0x00000010` / `0x00000020` / `0x00000040` | `NWAIT` / `LWAIT` / `TWAIT` | Wait sem espera / longa (RUMBA: 300 s) / temporizada (60 s, padrão) |
| `0x00001000` / `0x00002000` | `SRCHALL` / `SRCHFROM` | Busca no PS inteiro (padrão) / a partir da posição |
| `0x00004000` / `0x00008000` | `SRCHFRWD` / `SRCHBKWD` | Direção da busca |
| `0x00010000` / `0x00020000` | `FPAUSE` / `IPAUSE` | Pause de duração fixa / interrompível por evento do host |
| `0x00000200` / `0x00000400` | `AUTORESET` / `NORESET` | Enviar RESET antes do SendKey (padrão) / não |
| `0x00000800` / `0x00100000` | `CONPHYS` / `CONLOG` | Connect físico (traz a janela à frente) / lógico |
| — | `ESC=` | Caractere de escape dos mnemônicos (padrão `@`) |
| `0x00000040` (bug) | `QUIET` | Mesma máscara de `TWAIT`; **nunca emitido** pela DLL (o laço para no índice 22) |

Os valores coincidem 1:1 com as constantes oficiais `SSP_*` do `RDEEHLLAPI.dll` **[.NET]** (`SSP_NEWRET=1` …
`SSP_CONLOG=0x100000`, `SSP_DEFESC=0x40000000`). O wrapper .NET define ainda uma **segunda família** de bits
(`SSP_WRSUPER=1`, `WRWRITE=2`, `WRREAD=4`, `WRNONE=8`, `SUPERWRITE=16`, `RDWRITE=32`, **`QUIET=64`**, `NOQUIET=128`,
`NOSO`, `SO`, `SPACESO`, `EAD`, `NOEAD`, `DISPLAY`, `NODISPLAY`) — é daí que vem o `QUIET=0x40` que colide com `TWAIT`
na tabela da `ehlapi32`. Essas opções só podem ser enviadas como texto via `WD_SetSessionParamEx`.

Opções sem bit só por **`WD_SetSessionParamEx("A B,C")`** (separadas por espaço ou vírgula) [WDAPI]:

| Opção | Efeito [WDAPI] |
|---|---|
| `WRITE_SUPER` (padrão) | Pede escrita; só aplicações "supervisoras" podem conectar no mesmo PS → **outro cliente recebe rc 11** |
| `WRITE_WRITE` | Pede escrita e permite outras aplicações de escrita "previsíveis" |
| `WRITE_READ` | Pede escrita e permite outras só-leitura |
| `WRITE_NONE` | Acesso exclusivo |
| `SUPER_WRITE` | Supervisora: permite que aplicações de escrita compartilhem o PS |
| `CFGSIZE` (padrão) / `NOCFGSIZE` | `QuerySessions` devolve tamanho configurado / atual |
| `NOQUIET` (padrão) / `QUIET` | Mensagens de transferência de arquivo |
| `STREOT`, `EOT=c` | Strings terminadas por caractere em vez de tamanho |
| `NODISPLAY` | Documentado pela IBM/Attachmate, mas **o RUMBA rejeita** ("invalid parameter") — para ocultar senhas, consulte `WD_QueryFieldAttribute` [fórum Rocket] |

rc 2 = pelo menos um nome não reconhecido; os reconhecidos são aplicados mesmo assim [WDAPI].
Não foi encontrada nenhuma opção `TIMEOUT=` documentada para o RUMBA.

---

## 7. Estruturas de dados (formato 32-bit)

### 7.1 Query Session Status (20 bytes)  **[RE]** (usado internamente pelo `WD_CopyPS`)
| Offset | Tam | Campo |
|---|---|---|
| 0 | 1 | short name (entrada; `' '` = sessão conectada) |
| 1–3 | 3 | reservado |
| 4–11 | 8 | long name |
| 12 | 1 | tipo de sessão (`D` = display 3270, `E` = impressora 3270, `F`/`G` = 5250, `H` = ASCII) [IBM] |
| 13 | 1 | características (bit `0x80` = suporta EAB) |
| 14–15 | 2 | **linhas** (WORD) |
| 16–17 | 2 | **colunas** (WORD) |
| 18–19 | 2 | code page do host (WORD) |

### 7.2 Query Sessions (16 bytes por sessão, formato 32-bit)  **[.NET]** (struct `QuerySession`)
| Offset | Tam | Campo |
|---|---|---|
| 0 | 1 | `ShortName` |
| 1–3 | 3 | reservado |
| 4–11 | 8 | `LongName` |
| 12 | 1 | `Type` |
| 13 | 1 | reservado |
| 14–15 | 2 | `PSSize` (WORD: linhas×colunas) |

`*pCount` na entrada = número máximo de entradas que cabem no buffer; na saída = sessões encontradas.

### 7.3 OIA (Copy OIA, 103 bytes)  [WDAPI]+[IBM]
Byte 1 (offset 0) = formato (1 = 3270, 9 = 5250) · bytes 2–81 = imagem da linha de status (80 colunas) ·
bytes 82–103 = grupos de bits (numeração 1-based do manual):

| Grupo | Byte | Conteúdo |
|---|---|---|
| G1 | 82 | Posse da tela/online: setup, test, SSCP-LU, LU-LU, online não possuído, subsistema pronto |
| G2 | 83 | Seleção de caracteres |
| G3 | 84 | Shift (upper / numeric / CAPS) |
| G4–G6 | 85–87 | PSS / realce / cor (parte 1) |
| G7 | 88 | Insert |
| **G8** | **89–93** | **Input inhibited (5 bytes)**: byte 1 = machine check não-resetável, security key, machine check, communications check, program check, device busy, terminal wait, minus function/too much entered; byte 2 = wrong place, dead key, operator input error, system wait |
| G9–G11 | 94–96 | PSS / realce / cor (parte 2) |
| G12 | 97 | Erro de comunicação |
| G13 | 98 | Impressora |
| G14–G18 | 99–103 | Reservado / gráfico / gravação de teclas / enlarge |

Regra prática: qualquer bit ≠ 0 em G8 = teclado inibido; a imagem (bytes 2–81) mostra o texto `X SYSTEM`,
`X CLOCK`, `X -f` etc. rc da função: 0, 1, 2, 4 (ocupado), 5 (travado), 9. Mapa de bits exato **[a validar]**
com uma sessão real (o PDF tem a tabela parcialmente ilegível).

### 7.4 Janela da sessão (funções 103/104/106) e conversão (99)  **[.NET]+[IBM]+[RE]**
* `WD_QueryWindowCoordinates` — struct `QWCSTRUCT` (20 B): `ShortName(1) · Res(3) · Left · Bottom · Right · Top` (int32).
* `WD_WindowStatus` (28 B, tamanho confirmado no RE): `[0]` short name · `[4]` request (`WINDOWSTATUS_SET=1`,
  `QUERY=2`, `EXTQUERY=3` → 24 B) · `[6..7]` flags (`SIZE=0x1`, `MOVE=0x2`, `ZORDER=0x4`, `SHOW=0x8`, `HIDE=0x10`,
  `ACTIVATE=0x80`, `DEACTIVATE=0x100`, `MINIMIZE=0x400`, `MAXIMIZE=0x800`, `RESTORE=0x1000`) · `[8]` X · `[12]` Y ·
  `[16]` cx · `[20]` cy · `[24]` z-order (`FRONT=3`, `BACK=4`). O struct `WSSTRUCT` do wrapper .NET não bate com
  esses 28 bytes — confie no layout IBM. Na prática, prefira `WD_ShowSession` + `WD_GetSessionHWND` + Win32.
* `WD_ChangeWindowName(h, short, opt, nome)`: `CHANGEPSNAME_SET=1`, `RESET=2`.
* `WD_Convert(h, type, &POINT, short)`: `CONVERT_POSITION='P'` (80) posição → linha/coluna, `CONVERT_ROW='R'` (82) o inverso.

### 7.5 Atributo de campo (`WD_QueryFieldAttribute`)  [WDAPI]+[IBM]
Valor `0` = sem atributo (PS não formatado, rc 24). `0xC0–0xDF` = desprotegido, `0xE0–0xFF` = protegido.
Bits (0 = MSB): 0–1 sempre `11` · 2 protegido · 3 numérico · 4–5 intensidade (`00` normal, `01` normal +
detectável por caneta, `10` alta, `11` **não exibido** — campos de senha) · 6 reservado · 7 MDT (modificado).
Códigos de 2 letras para `WD_FindFieldPosition/Length`: `"T "` (este campo), `"N "`/`"P "` (próximo/anterior),
`"NP"`/`"PP"` (próximo/anterior protegido), `"NU"`/`"PU"` (próximo/anterior desprotegido).

### 7.6 Posições
`pos = (linha - 1) * colunas + coluna` (1-based). Para converter de volta use as colunas reais de
`QuerySessionStatus` (IMS A tem 32×80).

---

## 8. Mnemônicos de teclado (`WD_SendKey`)  **[RE]** (tabela do `WDApiSvc.ocx`)

| Tecla | Mnem. | Tecla | Mnem. | Tecla | Mnem. |
|---|---|---|---|---|---|
| Enter | `@E` | Clear | `@C` | Reset | `@R` |
| PF1–PF9 | `@1`…`@9` | PF10/11/12 | `@a`/`@b`/`@c` | PF13–PF24 | `@d`…`@o` |
| PA1 / PA2 / PA3 | `@x` / `@y` / `@z` | SysReq | `@A@H` | Attention | `@A@Q` |
| Tab / BackTab | `@T` / `@B` | Home | `@0` | New Line | `@N` |
| ↑ ↓ ← → | `@U` `@V` `@L` `@Z` | Backspace | `@<` | Delete | `@D` |
| Insert | `@I` | Erase EOF | `@F` | Erase Input | `@A@F` |
| Dup | `@S@x` | Field Mark | `@S@y` | Print | `@P` |
| Page Up / Down (roll) | `@u` / `@v` | Help | `@H` | `@` literal | `@@` |

Outros mnemônicos documentados [WDAPI]: `@q` End · `@A@T` Print PS · `@A@C` Test · `@A@J` Cursor Select ·
`@A@E` Field Exit · `@A@R` Device Cancel · `@$` cursor alternativo · `@/` queue overrun.

* A DLL rejeita ≥ 256 bytes (rc=2) [RE]; o manual fixa **128** como limite da função — quebre em pedaços de
  ≤ 128 [WDAPI]. Texto e mnemônicos podem ser misturados (`"SPRE@E"`), mas **tudo após a primeira tecla
  AID (Enter, PFn, PAn, Clear…) é descartado** [WDAPI] — uma AID por chamada.
* `CopyStringToPS`/`CopyStringToField` **não interpretam** mnemônicos (copiam `@E` literalmente) e não movem
  o cursor; só `SendKey` envia teclas de controle [WDAPI].
* O teclado precisa estar livre; com `AUTORESET` (padrão) o emulador envia RESET antes das teclas. A v2 liga
  `NORESET` logo depois do `ConnectPS` (teclado bloqueado vira `KeyboardLocked`; RESET só por pedido explícito).
* A v2 cobre todas as teclas desta tabela em `rumbapy.keys.Key` (PF1–24, PA1–3, SysReq, Attn, Erase Input, End,
  Field Exit, Dup, Field Mark…); a 0.1.5 (`api.py`, removido) não tinha PF13–24, PA1–3, SysReq, Attn etc.

---

## 9. Códigos de retorno

Nomes oficiais: constantes `API*` da classe `MicroFocus.RDE.Framework.EHLLAPI` (`RDEEHLLAPI.dll`) **[.NET]**;
uso por função conforme o padrão **[IBM]**.

| rc | Constante RUMBA | Significado | Onde aparece |
|---|---|---|---|
| 0 | `APIOK` | Sucesso (teclado livre, dado copiado, achado…) | todas |
| 1 | `APINOTCONNECTED` (`APIBLOCKNOTAVAIL`) | Não conectado / short name inválido | quase todas |
| 2 | `APIPARAMETERERROR` (`APIBLOCKIDINVALID`) | Erro de parâmetro (tamanho 0, string ≥ 256, opção inválida) | SendKey, CopyStringToPS, Copy*… |
| 3 | `APIFTXCOMPLETE` | Transferência de arquivo concluída | Send/ReceiveFile |
| 4 | `APIPSBUSY` (`APIFTXSEGMENTED`) | Host ocupado (X CLOCK/X SYSTEM); `Wait`: expirou ainda ocupado; `Connect`: conectou mas ocupado | Wait, SendKey, Connect, CopyPS* |
| 5 | `APIINHIBITED` | Entrada inibida/teclado travado; `CopyStringToPS`: **nada copiado** | Wait, SendKey, CopyStringToPS, Connect |
| 6 | `APITRUNCATED` | Dado truncado (campo protegido/fim do PS/buffer menor) | CopyStringTo*, CopyFieldToString |
| 7 | `APIPOSITIONERROR` | Posição inválida | Copy*, SetCursor, QueryFieldAttribute |
| 8 | `APINOTAVAILABLE` | Sem `Start…` prévio / indisponível | QueryHostUpdate, Stop*, GetKey |
| 9 | `APISYSERROR` | Erro de sistema (também o valor pré-carregado quando o emulador não responde) | todas |
| 10 | `APINOTSUPPORTED` | Função não suportada | — |
| 11 | `APIUNAVAILABLE` | Recurso indisponível / PS em uso por outra aplicação | Connect |
| 12 | `APISESSIONSTOPPED` | Sessão encerrada | — |
| 13 | `APINOTENABLED` | Recurso não habilitado | — |
| 20 | `APIUNDEFINEDKEY` | Mnemônico indefinido | SendKey |
| 21 / 22 / 23 | `APIOIAUPDATE` / `APIPSUPDATE` / `APIBOTHUPDATE` | OIA / PS / ambos atualizados | QueryHostUpdate |
| 24 | `APINOFIELD` | Não encontrado / PS não formatado | SearchPS, SearchField, FindField*, Copy*Field |
| 25 | `APINOKEYSTROKES` | Fila de teclas vazia | GetKey |
| 26 | `APIPSCHANGED` | PS/OIA mudou durante Pause (IPAUSE) | Pause |
| 27 | `APIFTXCANCELLED` | Transferência cancelada | Send/ReceiveFile |
| 28 | `APIZEROLENFIELD` | Campo de tamanho 0 | FindFieldLength/Position |
| 30 | `APIINVALIDTYPE` | Tipo inválido | — |
| 31 | `APIKEYOVERFLOW` | Estouro da fila de teclas | KS intercept |
| 32 | `APISFACONN` | Já conectado (structured fields) | — |
| 34 / 35 | `APITRANCANCLI` / `APITRANCANCL` | Transação cancelada | — |
| 36 | `APIHOSTCLOST` | **Conexão com o host perdida** | — |
| 37 | `APIOKDISABLED` (`APIDISKFULL`) | OK, função desabilitada / disco cheio (FTX) | — |
| 38 | `APINOTCOMPLETE` | Não concluído | — |
| 39–43 | `APISFDDM`, `APISFDPEND`, `APIBUFFINUSE`, `APINOMATCH`, `APILOCKERROR` | Structured fields / buffer / lock | — |
| 301–311 | `APIINVALIDFUNCTIONNUM` (301), `APIFILENOTFOUND` (302), `APIACCESSDENIED` (305), `APIMEMORY` (308), `APIINVALIDENVIRONMENT` (310), `APIINVALIDFORMAT` (311) | WinHLLAPI | — |
| 9998 / 9999 | `APIINVALIDPSID` / `APIINVALIDRC` | Sessão/rc inválidos | — |

Na v2, cada rc de cada função vira uma exceção tipada em `src/rumbapy/_worker/rc.py` (teste percorre a tabela
inteira); a 0.1.5 (`api.py`, removido) cobria só um subconjunto.

---

## 10. Armadilhas confirmadas e recomendações

1. **Buffers:** `create_string_buffer(n).value` corta no primeiro `NUL` — use `.raw[:n]` e troque `\0` por espaço.
2. **Encoding:** o texto é **cp1252** (ANSI), **não UTF-8** — confirmado ao vivo em 2026-10-03 (o byte `0xE7`
   gravado com `CopyStringToPS` é exibido como `ç`; ida e volta preserva `ç ã É €`). Use `errors="replace"`.
3. **`WD_QuerySessionStatus` precisa de buffer mutável de 20 B** com a letra no byte 0.
4. **`WD_CopyPS`** escreve linhas×colunas bytes (2560 no IMS A) — aloque pelo `QuerySessionStatus`.
5. **`WD_SendKey` > 255 bytes** → rc=2; quebre textos longos.
6. **Sem prazo**: uma chamada pode bloquear para sempre; só um processo novo resolve (ver §4).
7. **Popups modais da DLL** (`MessageBoxA`): "Exceeded maximum number of clients" — evite threads
   descartáveis e `TerminateProcess`.
8. A DLL só enxerga sessões publicadas por perfis com HLLAPI (`index_pro_api.html`).
9. Não confie no `hApp` para separar conexões — a separação é por processo.
10. **"Host pronto" ≠ `WD_Wait` == 0.** O manual é explícito: espere por um texto-chave da próxima tela ou pela
    posição do cursor; use `Wait`/OIA só para saber se o teclado aceita digitação.
11. **Uma tecla AID por `SendKey`**; o resto da string é descartado.
12. Chame **`WD_ResetSystem` antes de sair** (o manual manda; libera reservas, notificações e intercepts).
13. Parâmetros de saída (`LPWORD`) em ctypes: `ctypes.byref(c_ushort())` — passar inteiro por valor (erro do
    AutoRumba/Java) nunca devolve nada.
14. `WD_Convert` devolve o resultado **no próprio retorno** (0 = inválido, 9998/9999 = erro), não em rc.
15. Um 2º cliente no mesmo PS recebe rc 11 sob `WRITE_SUPER` (padrão) — no máximo um worker por letra.
16. **Fechar um emulador derruba a conexão HLLAPI dos outros clientes** (de outras letras): a próxima chamada dá rc 1;
    reconecte (`ConnectPS` da mesma letra) e confira o HWND antes de continuar (§11.1.1).
17. **`WD_QuerySessions` pode travar** se chamado enquanto (ou logo depois que) um emulador encerra: espere a saída do
    processo sem chamar a DLL e use prazo curto (§11.1.1).
18. `WM_CLOSE` com o modal "Short Name already in use" aberto pode derrubar o launcher (`0xC0000005`): clique OK antes.

---

## 11. Outras interfaces do RUMBA (resumo)

* **`WHLLAPI.dll`** (83 exports): `WinHLLAPI`, `WinHLLAPIStartup/Cleanup`, `WinHLLAPIAsync`,
  `WinHLLAPISetBlockingHook`, `WinHLLAPICancelBlockingCall`, e as mesmas `WD_*` + `WD_*Async`,
  `WD_Startup`, `WD_Cleanup`, `WD_CancelAsyncRequest`, `WD_AdviseRumbaEvent`, `WD_StartCloseIntercept`.
* **DDE** (servidor no `WDApiSvc.ocx`): serviço `RUMBADSP` (mainframe), itens de PS, OIA (`KBD`,
  `READY`, `BND`), cursor, campos, busca, cópia de bloco e *advise* (`UpdatePS`, `UpdateOIA`).
* **ActiveX ObjectX** (`WdMfDsp.ocx`, `ProContainer.ocx`, `WDApiSvc.ocx`), **.NET RDE**
  (`Microfocus.RDE.Framework.dll`, `RumbaRDEFactory.dll`, `RDEEHLLAPI.dll` — este último é um P/Invoke
  oficial da própria `ehlapi32.Dll`, útil como confirmação de assinaturas) e **RumbaScriptAT**:
  ver `RUMBA_AMBIENTE.md` §4.1–4.5.
* Code pages de host por perfil (`HostCharacterSet`): BRAZIL D/Z = 56 `CHARSET_SWEDISH_EURO` (**CCSID 1143**),
  IMS A = 0 `CHARSET_USA` (**CCSID 037**). O emulador converte EBCDIC → code page do PC antes de entregar ao HLLAPI.
* **64-bit oficial existe só em produtos mais novos:** Rumba+ Desktop 10.2 SP1+ traz `EHLAPI64.DLL` e o
  proxy `RumbaAddon64.exe` (o SP3 corrigiu RUMBA-4675 "EHLLAPI DLL 32-bit to 64-bit conversion problems").
  **Não estão instalados aqui** (W2H Pro 6.9.4 / componentes 10.2 GA). Rumba+ Desktop com VBA também expõe
  COM `MicroFocus.Rumba` — idem, não disponível no W2H.
* *Trace* oficial: Rumba+ Trace → "Rumba+ Display APIs (includes DDE, EHLLAPI, and WinHLLAPI)"
  (`rumbatrc.ini` entrada 300).

---

## 11.1 Validado ao vivo (2026-10-03, BRAZIL_D via Web Launcher, sem teclas AID)

| Item | Resultado |
|---|---|
| Registro da letra após lançar o launcher | ~9,4 s (`WD_QuerySessions`: `44 20…20 48 20 80 07` → `D`, tipo `H`, PS 1920) |
| `WD_ConnectPS` | **rc=1 enquanto a TN3270 conecta** (diálogo "Telnet3270 Application — Connecting To Address") → rc=0 em ~11 s |
| `WD_Wait` após conectar | rc=0 imediato |
| `WD_QuerySessionStatus` | `44 20 20 20 · 44 00 20×6 · 44 · 00 · 18 00 · 50 00 · 00 00` → tipo `D`, 24×80 (layout do §7.1 confirmado) |
| `WD_CopyOIA` | rc=0, formato 1; a imagem da linha de status usa **códigos internos** (não é texto cp1252); G1=`0x10` (LU-LU), G8 zerado com teclado livre |
| `WD_QueryCursorLocation` / `WD_QueryFieldAttribute` | 1736 = (22,56) / `0xC5` (desprotegido, MDT) |
| `WD_GetSessionHWND` | HWND da **janela-raiz** do launcher (`"BRAZIL_D - Pro Client"`, mesmo PID) |
| `WD_ShowSession(6/9)` | rc=0, mas **não** minimiza a janela do launcher → usar `user32.ShowWindow` nesse HWND |
| Janela oculta (`SW_HIDE`) ou minimizada | a API continua funcionando |
| Encoding | **cp1252** (screenshot do `ç`) |
| 2ª instância da mesma letra | diálogo modal **"RUMBA API Service Control — Short Name already in use"**; o HLLAPI segue na 1ª |
| `WM_CLOSE` na janela do launcher | fecha em ~0,5 s sem confirmação; a letra é desregistrada |

### 11.1.1 Medições da v2 contra a DLL real (2026-10-03/04, worker 32-bit + guard, sem AID fora do L6)

Roteiros da especificação (J.6) executados pelo agente principal com autorização do dono: L1, L2, L3, L4, L5, L6, L8,
L9 e o roteiro "compat" (script da 0.1.5 sem alteração); L3b em 2026-10-04 com a sonda avulsa anterior (`ConnectPS`
rc 0 em ~14 ms depois de `os._exit(3)` sem `DisconnectPS`, sem rc 11). **Não executado:** L7 (IMS/RHELP). Ferramentas
(hoje todas invólucros de `tests/live/runner.py`): `tools/research/live_worker_probe.py`, `live_v2_probe.py`,
`live_close_probe.py`, `live_legacy_probe.py`, `live_l3b_probe.py`.

| Item | Resultado |
|---|---|
| Boot do worker até `listening` | ~1,2 s; 1º `WD_QuerySessions` 74 ms (carga da DLL) |
| Registro da letra depois do `Popen` do launcher | **~9 a ~25 s** (varia entre execuções; um `QuerySessions` levou 0,8 s no instante do registro) |
| `WD_ConnectPS` rc 0 depois do `Popen` | **~11 a ~27 s** (rc 1 por alguns segundos enquanto a TN3270 conecta); `open("D")` completo ~17,6 s numa execução típica |
| `WD_SetSessionParamEx("NORESET")` | rc 0 (parâmetro local da conexão HLLAPI) |
| 1ª tela | já presente no `connect` pelo launcher; pelo fallback IE, rc 4 (PS vazio) logo depois do `connect` |
| `QuerySessions` / `QuerySessionStatus` | tipo `H`, nome longo vazio / tipo `D`, nome longo `D`, 24×80, `host_cp` 0, EAB desligado |
| `WD_GetSessionHWND` | janela-raiz do launcher; `hwnd_pid` = PID do launcher |
| `snapshot` com enumeração de campos | 1º: 38 ms no worker (26 campos, 1 de entrada em (22,56) atributo `0xC5`); seguintes 1,6–2,9 ms no worker |
| `snapshot` ida e volta 64-bit → pipe → 32-bit (com máscara) | **~2–3 ms** (mediana 2,7–2,8 ms; mín 2,5; máx 3,8) |
| Escrita local (L4) | `ç`, `€`, `X` lidos de volta iguais; `Truncated`/`ProtectedField` recusados sem cópia (`delivery="none"`) |
| Logon CICS (L6, guard `login`) | menu → `C` → signon (senha em campo **oculto**) → `Signon` em 1,17 s; senha ausente de todo log/repr/relatório |
| Fechamento (`WM_CLOSE` só na janela principal) | janela some em 0,11 s, processo sai em 0,33 s; `close` completo **~0,4–0,56 s** (também com sessão logada, sem diálogo) |
| `WD_QuerySessions` depois que o processo do emulador saiu | 1 ms e a letra já não está registrada — mas **travou** (intermitente) em outras execuções, inclusive logo depois do fechamento: o watchdog encerrou o worker (por isso o `close` hoje não consulta a DLL quando não precisa e o limite dessa função é 8 s) |
| 2ª instância da mesma letra (L2) | "RUMBA API Service Control — Short Name already in use" (às vezes 2 vezes); a 2ª instância sai com `0xC0000005` ao fechar, com ou sem o OK antes |
| Dois perfis (L9) | `BRAZIL_D` e `BRAZIL_Z` em **processos de launcher diferentes** (um processo por perfil); abrir o D não afeta o Z; **fechar o D derruba a conexão HLLAPI do worker do Z** (rc 1 na próxima chamada, janela do Z intacta) |
| Lease entre processos (L3) | 2º dono → `TerminalInUse` com PID/script; `close` com dono vivo em ~0,56 s (o outro vira `replaced`); reciclagem do worker → `read` seguinte em 0,76 s |
| Servidor 0.1.5 real (L8) | `disconnect` pela neutralização em 61 ms; ele continua escutando, sem a conexão HLLAPI. Obs.: o `python.exe` de venv é redirecionador (PID ouvinte ≠ PID do `Popen`) |
| Log do worker | nenhuma linha com texto de tela; só coordenadas, tamanhos, rc e (DEBUG) `sha=` |

Inventário de janelas do processo do launcher (16 de nível superior numa sessão aberta): 2 visíveis — a principal
WindowsForms `"<PERFIL> - Pro Client"` e uma `Internet Explorer_Hidden` **sem título mas com `WS_VISIBLE`** — e ocultas:
"RUMBA API Manager" (Afx, serviço HLLAPI), "Telnet3270 Application", `WinSocketNotifyWindow`/`SocketNotifyWindow`,
`.NET-BroadcastEventWindow*`, `GDI+ Hook Window Class`, `IME`/`MSCTFIME UI`. Só a principal recebe `WM_CLOSE`.

## 12. Fontes

| Fonte | Onde |
|---|---|
| Desmontagem da `ehlapi32.Dll`/`WHLLAPI.dll`/`WDApiSvc.ocx` 10.2.1710 | `tools/research/rdis.py`; notas brutas em `docs/interno/referencia/ehlapi32_notes.md` (não versionado) |
| P/Invoke, structs e constantes oficiais (`RDEEHLLAPI.dll`) | dump em `docs/interno/referencia/dotnet/RDEEHLLAPI.api.txt` (não versionado) |
| Type libraries (ObjectX, ProContainer, HLLAPI control, RumbaScriptAT) | `docs/interno/referencia/tlb/` (não versionado) |
| "WD_API Language Reference", Attachmate, 2004 (83 p.) | cópia pública: `https://github.com/panther999/AutoRumba/blob/master/src/main/resources/wd_api.pdf`; cópia local em `docs/interno/referencia/fontes/` |
| Documentação atual do RUMBA (agora da **Rocket Software**) | `https://docs-be.rocketsoftware.com/bundle?cluster=true&labelkey=prod_rumba` |
| Rumba+ Desktop 10.2 SP1 System Administrator Guide | `https://docs-be.rocketsoftware.com/bundle/rumba_ag2_102sp1_pdf/...` (cópia local em `fontes/`) |
| Blue Prism Mainframe Guide – Micro Focus RUMBA (opções API: "Generate PS Update on all keystrokes", "Classic HLLAPI data structure") | `https://documentation.blueprism.com/bp-7-3/en-us/z-pdf/mainframe/blue-prism-mainframe-guide-microfocus-rumba.pdf` |
| Fórum Rocket (NODISPLAY rejeitado; ObjectX fora do navegador; VB.NET/WinHLLAPI) | `https://community.rocketsoftware.com/rocket-host-access-24/` |
| Relatório completo da pesquisa web (com etiquetas DOC/FORUM/INFERRED) | `docs/interno/referencia/fontes/web_research.md` |

Contradições conhecidas no manual WD_API: `LWAIT` = "até 5 minutos" numa página e "até liberar" noutra
(na `WHLLAPI.dll` do RUMBA o código usa **300.000 ms**); limite do `SendKey` = 255 numa página e 128 noutra.

# O ambiente RUMBA da Volvo — componentes, perfis, processos e caminhos de automação

> Levantado em 2026-10-03 numa estação Windows 11 Enterprise (build 26200) com o RUMBA instalado
> pelo próprio portal Web-to-Host. Tudo aqui foi **observado** (arquivos, registro, janelas, páginas
> do servidor), salvo quando marcado como **[a validar]**. A referência da DLL está em
> [`RUMBA_EHLLAPI.md`](RUMBA_EHLLAPI.md).

---

## 1. Qual "RUMBA" é este

| Item | Valor |
|---|---|
| Produto do portal | **OnWeb Web-to-Host Pro** ("Pro Client"), versão **600.94.1710.0** (`REL60094`) |
| Componentes de emulação | **Micro Focus Rumba+ Desktop 10.2.1710.0** ("10.2 GA DECO 23861"), © 1984-2022 |
| Origem histórica | Wall Data (prefixo `WD`, `WallData.*` ProgIDs) → NetManage (`NetManage.ProControlContainer`) → Attachmate/Micro Focus → OpenText → **Rocket Software** (dona atual; documentação em `https://docs-be.rocketsoftware.com/bundle?cluster=true&labelkey=prod_rumba`, links antigos `microfocus.com/documentation/rumba` dão 404) |
| Arquitetura | **100% x86 (32-bit)**, inclusive assemblies .NET (machine `0x14c`) |
| Portal | `https://rumba.srv.volvo.com/rumba694/w2hlegacy/` (IIS 10) |
| Instalação/atualização | `NMServiceManager.exe` (serviço) baixa MSIs de `.../w2hlegacy/w2hlegacy/` sob demanda (log em `C:\Program Files (x86)\Micro Focus\NMServiceManager\Logs\NMInstall.log`) |

### 1.1 Diretórios
```
C:\Program Files (x86)\Micro Focus\
├─ RUMBA\
│  ├─ System\     ehlapi32.Dll (EHLLAPI), WHLLAPI.dll (WinHLLAPI), WDApiSvc.ocx (servidor HLLAPI/DDE),
│  │              WdMacCtl.ocx, WdAppCtl.ocx, WdFtxCtl.Ocx, Microfocus.RDE.Framework.dll (+ .XML),
│  │              RDEHLLAPI.dll, RDEEHLLAPI.dll (+ .XML), ObjectXHLLAPI.dll, rumbatrc.ini (trace), ...
│  ├─ MFrame\     WdMfDsp.ocx + WdMfDsp.tlb (display 3270), EngMFWin.dll (engine 3270),
│  │              WdTelnet.dll / WDTnLib.dll (TN3270), ObjectXMFDisplay.dll, RDEMFDisplay.dll, Charsets\
│  ├─ w2hlegacy\  ProContainer.ocx (contêiner "Pro Control Container"), SysCheck.dll, hostpro2.ini
│  ├─ AS400\ Hp\ vt\   emuladores 5250 / HP / VT (não usados pelo projeto)
├─ Host Access Web Launcher\Host Access Web Launcher.exe   (protocolo hawlauncher://)
├─ NMServiceManager\  serviço de download/instalação dos componentes W2H
└─ RSS\               Micro Focus Shared Security (proxy/SSL/SSH)
C:\Program Files (x86)\Micro Focus\RumbaWebToHost.vbs      atalho: abre index_pro.html no Edge
```

### 1.2 Componentes COM registrados (todos `HKCR\WOW6432Node\CLSID`, `InprocServer32`, Apartment)
| CLSID | ProgID / nome | Arquivo |
|---|---|---|
| `{913B101A-EDD1-11D5-AD4A-0060B0FB4900}` | `NetManage.ProControlContainer.1` — "ObjectX Pro Control Container" | `w2hlegacy\ProContainer.ocx` |
| `{91858523-87B5-11CE-A0B6-0800095A05C3}` | `WallData.MainframeDisplay.12` — "ObjectX Mainframe Display Control" | `MFrame\WDMFDSP.OCX` |
| `{D76BF085-4D42-11CF-A0CD-08005A4B1DD1}` | `WallData.HLLAPI.4` — "ObjectX HLLAPI Control" | `System\WDAPISVC.OCX` |
| `{2CEA48E0-9782-11CE-8FDE-08005AD009BB}` | Mainframe Printer | `MFrame\WDPRNMF.OCX` |
| `{1309F3EA-5A97-437A-9D08-19CD05EEE7C0}` | VBA Scripting | `WDScript.ocx` |
| `{8E4800C0-3CC7-438C-9711-B21EA0CC0E4A}` | `W2HDownloadInstall.Downloader.*` (serviço de download) | NMServiceManager |
| `{71BC2ED7-60F8-40c7-816C-3BF05DB994A3}` | objeto de persistência do Pro Client | — |
| `{D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E}` | `InternetExplorerMedium` (usado pelo fallback IE da v2, `_ie_open.js`; era o caminho da 0.1.5) | `ielowutil.exe` → IE |

Protocolo de URL: `HKCR\hawlauncher\shell\open\command` = `"C:\Program Files (x86)\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.exe" "%1"`.

---

## 2. O portal e os perfis

### 2.1 Páginas de índice
| Página | Uso |
|---|---|
| `index_pro.html` | Atalho do usuário (`RumbaWebToHost.vbs` → Edge). Perfis **sem** HLLAPI (sessões 3270 de uso manual). |
| `index_pro_api.html` | **Perfis com HLLAPI** — o que o RumbaPy usa. Links `profpro/<PERFIL>.html` abertos com `window.open(..., width=905,height=650)`. |

Perfis em `index_pro_api.html`: AS/400 (`As400_VF05_B` … `As400_VFA1_C`), UNIX (`UD80_A` … `UD81_C`) e
mainframe: `IMS_A`, `IMS_F`, `IMS_S`, `BRAZIL_D`, `BRAZIL_Z`, `TerminalUME_A`.

### 2.2 Parâmetros dos perfis usados (de `profpro/*.html`, bloco `ConfigWizardSettings`)
| Parâmetro | BRAZIL_D | BRAZIL_Z | IMS_A |
|---|---|---|---|
| `HostInterface` / `InterfaceName` | Telnet / `WallData.TN3270Link` | idem | idem |
| `DestinationAddress:Port` | host CICS-BR | host CICS-BR | host IMS-SE |
| TLS (`UseSSL`, `SecSSLEnabled`) | ver `docs/interno/` | idem | idem |
| `LUSelection` / `LUName` | 0 / vazio (pool) | idem | idem |
| `StartupModel` | **2** (24×80) | 2 | **3** (32×80) |
| `HostCharacterSet` | **56** = `CHARSET_SWEDISH_EURO` → EBCDIC **CCSID 1143** | 56 | **0** = `CHARSET_USA` → EBCDIC **CCSID 037** |
| `ShortName` / `HLLAPIShortName` | **4** → `D` | **26** → `Z` | **1** → `A` |
| `WindowTitle` | ` [D]` | ` [Z]` | ` [A]` |
| `HLLAPIEnabled` | true | true | true |
| `EHLDLLName` / `WHLDLLName` | `EHLAPI32.DLL` / `WHLLAPI.DLL` | idem | idem |
| `HllapiStandard` | false | false | false |
| `CopyStringTruncate` / `KeyPSUpdate` | true / true | idem | idem |
| `ConvertNulls` / `SearchBackPosition` / `DeleteSession` | false / false / false | idem | idem |
| `DDETopic` | vazio (servidor DDE `RUMBADSP`) | idem | idem |
| `StandAlone` / `AllowMultipleStandAlone` | true / true | idem | idem |
| `AutoConnect` / `AutoReconnect` | true / false | idem | idem |
| `TypeAhead` / `KeyboardAutoReset` | true / false | idem | idem |
| `ExtendedAttributes` / `TN3270Only` | true / false (TN3270E permitido) | idem | idem |
| `InactivityTimeout` / `SessionTimeout` | 0 / 0 | idem | idem |
| `kc1..kc4`, `DevID` | chaves de licença por perfil | — | — |

Regra do short name: o número do perfil é o **índice da letra** (1=A … 26=Z). O título da janela
recebe o sufixo `[D]`/`[Z]`/`[A]`, e o título da página é `"<PERFIL> - Pro Client"`.

### 2.3 O que o JavaScript do perfil faz (`/rumba694/w2hlegacy/config/procontainerfunc.js`)
1. `LoadW2HDownloadService()`:
   * **Navegador sem ActiveX** → redireciona para `hawlauncher://<url-do-perfil>/microfocus_w2h_hawlauncher`.
   * **IE, `bStandAlone` e sem `window.opener`** e fora do Web Launcher → `DoStandAlone()` (reabre a mesma URL num popup 800×600 e mostra "separate window").
   * Senão (popup, ou dentro do Web Launcher via `window.external.IsHostAccessWebLauncher()`) → cria o
     `W2HDownloadInstall.Downloader`, que confere/instala os componentes `hostproprereqs,pro_container,cphostpromd,md0409[,cphostprohllapi]`.
2. `CreateProObject()` → `<object classid="CLSID:913B101A-…">` (`ProClient`).
3. `InitProClientIE()` → `ProClient.setActive()`, propriedades do contêiner (`SourceName`, `DevID`, licenças,
   `HLLAPIShortName`, `SeparateWindow=bStandAlone`…), `ProClient.CreateSession(1)` (1 = display mainframe),
   propriedades Telnet (`ObjectXTelnetInterface`: host/porta), display, **HLLAPI** (`ObjectXHLLAPIInterface`:
   `ShortName`, `EHLDLLName`, `ConvertNulls`, `KeyPSUpdate`, `CopyStringTruncate`, `HllapiStandard`,
   `DDEServer="Rumbadsp"`, `HllapiMulSession=false`…), segurança (`ObjectXSecurityInterface`) e
   `ProClient.Connect()`.

Erros do script: `Terminate(1006)` (navegador não suportado), `1007` (falha de download/criação),
`1008` (exceção na inicialização), `1009` (objeto sem `SourceName`).

---

## 3. Processos e janelas de uma sessão em execução

Observado com Edge em **modo IE** (`iexplore.exe -dualengineadapter=… APPID:MSEdge`, filho do `msedge.exe`):
```
iexplore.exe (x86)                                   ← processo de abas do IE (32-bit)
└─ TabWindowClass "https://rumba.srv.volvo.com/ - <PERFIL> - Pro Client - Internet Explorer"
   └─ Shell DocObject View
      └─ Internet Explorer_Server
         └─ AfxOleControl140                         ← ProContainer.ocx
            └─ AfxWnd140 "CContainer2Ctrl - Control Container"
               └─ AfxFrameOrView140 "Display Frame"
                  ├─ "W2HMenuBar", ToolbarWindow32 "Main", msctls_statusbar32
                  └─ AfxFrameOrView140
                     └─ AfxOleControl140 (1906×899)  ← WdMfDsp.ocx (a tela 3270)
                        └─ 4× AfxOleControl140 0×0   ← controles auxiliares (HLLAPI, macro, FTX, app) [a validar qual é qual]
Janelas soltas do mesmo processo: DDEMLEvent / DDEMLMom (DDE), JClipView (clipboard)
```
* Com `InternetExplorerMedium` (o fallback IE da v2; o caminho padrão da 0.1.5), a janela é um IE "frame" próprio (popup do `window.open`).
* A sessão usa um *socket* local em escuta no próprio processo (`127.0.0.1:<efêmera>`), coerente com o
  comentário do perfil "all connection go via secure redirector" (camada `Rnsocks.dll`/RSS) **[a validar]**.

### 3.1 Sessão aberta pelo Host Access Web Launcher (caminho padrão da v2; validado ao vivo em 2026-10-03/04)

* **Um processo `Host Access Web Launcher.exe` por perfil**: com `BRAZIL_D` e `BRAZIL_Z` abertos, são dois processos
  (roteiro L9). Código de saída 0 do `Popen` não é falha (o RumbaPy não depende disso).
* Inventário do processo (16 janelas de nível superior numa sessão aberta):

  | Janela | Classe | Visível | Papel |
  |---|---|---|---|
  | `<PERFIL> - Pro Client` | `WindowsForms10.Window.*` | sim | janela principal (raiz do `WD_GetSessionHWND`) — a **única** que recebe `WM_CLOSE` |
  | (sem título) | `Internet Explorer_Hidden` | **sim** (`WS_VISIBLE`) | auxiliar do motor IE; fechá-la junto atrapalhava o encerramento |
  | `RUMBA API Manager` | `Afx:*` | não | serviço HLLAPI do emulador |
  | `Telnet3270 Application` | — | não (vira o diálogo "Connecting To Address" enquanto conecta) | conexão TN3270 |
  | — | `SocketNotifyWindow`/`WinSocketNotifyWindow`, `.NET-BroadcastEventWindow*`, `GDI+ Hook Window Class`, `IME`, `MSCTFIME UI` | não | auxiliares do Windows/.NET |

  O RumbaPy ignora as auxiliares (`_instances.HELPER_CLASSES`).
* Diálogos: `Telnet3270 Application` / "Connecting To Address: …" (`#32770`, botão Cancel) aparece por alguns
  segundos enquanto a TN3270 conecta e some sozinho — não tocar; `RUMBA API Service Control` / "Short Name already in
  use" (botão OK) quando a letra já está em uso por outra instância — o `close` clica OK **antes** do `WM_CLOSE` da
  dona, porque `WM_CLOSE` com o modal aberto derrubou o launcher (`0xC0000005`); `HLLAPI ERROR` / "Exceeded maximum
  number of clients" — nunca clicar (exige logoff).
* Tempos: letra registrada no HLLAPI ~9 a ~25 s depois do `Popen`; `WD_ConnectPS` rc 0 ~11 a ~27 s; `WM_CLOSE` na
  principal: janela some em ~0,1 s, processo sai em ~0,3 s, letra desregistrada; sessão CICS logada fecha sem
  diálogo de confirmação.
* **Fechar uma sessão derruba a conexão HLLAPI dos clientes de outras letras** (a janela delas continua aberta e
  logada): a próxima chamada dá rc 1 e é preciso reconectar (`docs/RUMBA_EHLLAPI.md` §11.1.1). Abrir não derruba.
* `WD_QuerySessions` chamado enquanto o emulador encerra (e, às vezes, logo depois de o processo sair) pode travar
  dentro da DLL.
* `ShowWindowAsync(SW_SHOWMINNOACTIVE)` minimiza sem ativar e o HLLAPI continua funcionando; `WD_ShowSession` não
  minimiza esta janela.

---

## 4. Caminhos de automação existentes (inventário)

| Caminho | Bitness do cliente | Requisitos | Observações |
|---|---|---|---|
| **EHLLAPI `ehlapi32.Dll` (`WD_*`/`hllapi`)** — usado hoje | 32-bit | perfil com HLLAPI | Conexão por processo (ou por thread com `MultiThreadConnectionOn`); IPC por memória compartilhada + window message; espera infinita. Ver `RUMBA_EHLLAPI.md`. |
| **WinHLLAPI `WHLLAPI.dll`** | 32-bit | perfil com HLLAPI | Mesma família; `WinHLLAPIStartup/Async`, *blocking hooks*, `WD_CancelBlockingCall`; esperas IPC **finitas** (N×500 ms); `Wait` com TWAIT=60 s / LWAIT=300 s. |
| **DDE (`RUMBADSP`)** | **32 ou 64-bit** | perfil com HLLAPI | `WDApiSvc.ocx` implementa servidor DDE (itens de PS, OIA, cursor, busca, campo, bloco, *advise*). Única via oficial que dispensa Python 32-bit. **[a validar tópicos]** |
| **ActiveX/COM (ObjectX)** | 32-bit, in-process | hospedar o controle | `ProContainer` (não licenciado) + `WdMfDsp` (licenciado) são scriptáveis (o próprio portal faz isso em JS). Hospedar no nosso processo (ex.: ATL `AtlAxWin`) eliminaria o IE. Ver §4.1–4.3. |
| **.NET RDE (`Microfocus.RDE.Framework`, `RumbaRDEFactory`, `RDEEHLLAPI`)** | 32-bit (ou AnyCPU `_post2019`) | .NET Framework 4/4.8 | Wrappers gerenciados; `RDEFactory` modo `StandAlone` hospeda o display num WinForms. Ver §4.4. |
| **Host Access Web Launcher (`hawlauncher://`)** | — (lançador) | instalado | Abre um perfil W2H **sem** automatizar o IE, num processo próprio. Linha de comando confirmada no IL (§4.5) e **validada ao vivo** (BRAZIL_D registrado em ~9 s; ver `RUMBA_EHLLAPI.md` §11.1). |
| **RumbaScriptAT** (estilo EXTRA!) | 32-bit | motor de scripts RUMBA | `WaitForString`, `WaitHostQuiet`… (§4.4). Uso externo não validado. |
| **`WD_RunProfile`** | 32-bit | — | Para URL `http://` chama `ehlhttp!ExecuteHttpProfile` → `WinExec("<navegador padrão> -new <url>")`; não serve para automação robusta. |
| **TN3270 direto** (sem RUMBA) | **64-bit**, qualquer SO | rede até `host:23` + aprovação | Mais rápido/robusto/headless; exige replicar host, modelo e code page e **aprovação de segurança/compliance**. Ver plano, Fase 6. |

### 4.1 API COM do display 3270 (`WallData.MainframeDisplay.12`, coclass `ObjectXMFDisplay`)  [TLB]
Controle **licenciado** (`IClassFactory2`), interface padrão `IObjectXMFDisplay20` (dual), eventos `IObjectXMFDisplayEvents`.
| Grupo | Membros (DISPID) |
|---|---|
| Tela | `GetScreen2(Row, Col, Length, [vTranslateCharacter], [vConvertAttributesToSpaces]) -> BSTR` (0x5A), `PutScreen2(BSTR, Row, Col)` (0x5B), `GetScreen`/`PutScreen` (5/7), `GetScreenAttributes2` (0xC7), `GetWordAtPosition` (0x1D), `Rows` (0x2F), `Columns` (0x22) |
| Teclado | `SendKeys(BSTR)` (8), **`RDE_SendKeys_Hsynch(Keys, bWaitForEvents, nWaitCount, nTimeOut, szVerString, Row, Col) -> APIReturnValueConstants`** (0xB6), `Keyboard` (0x51: `Locked`, `TypeAhead`, `AutoReset`) |
| Conexão | `Connect()` (1), `Disconnect()` (3), `RDE_Connect_Hsynch(...)` (0xB7), `HostConnected` (0x29), `ReadyToSend` (0x2E), `BindState` (0x48), `HostIPAddress` (0xAE) |
| Cursor | `CursorRow`/`CursorColumn` (0x4A/0x4B), `GetCursorPosition`/`SetCursorPosition` (0xD4/0xD5) |
| Campos | `Fields` (0x3F) → `IObjectXHostFields`/`IObjectXHostField` (`Text`, `StartRow/Col`, `EndRow/Col`, `Length`, `Protected`, `Hidden`, `ModifiedDataTag`, `Intense`), `FieldCount` (0xC8), `GetFieldInfo` (0xC9) |
| OIA | `GetStatusLine([out] Status, [out] GroupIndicators)` (4) |
| Host/Telnet | `HostInterface` (0x2B) → `IObjectXHostInterface2` → `Interface` = `IObjectXConfigureTn3270Link6` (`WallData.TN3270Link.1`: `TelnetPort`, `DeviceName` (LU), `TerminalType`, `SslEnabled`, `ConnectionTimeout`, `AutoReconnect`, `KeepAliveMode`) |
| Janela | `WindowHandle` (-515) |
| Eventos | `AfterConnect`, `AfterDisconnect`, `BeforeConnect/Disconnect(Cancel)`, `CursorMove`, `EndOfData`, `KeyboardLock(state)`, `LocalScreenChange`, `ReceiveData`, `ReadyToSend(state)`, **`HostScreenChange(ScreenType, ScreenId, StartRow, StartCol, EndRow, EndCol)`**, `HostStateChanged`, `HostConnectionChanged`, `InNewField`, `HostKeyPress2` |

`APIReturnValueConstants` (usado pelos `RDE_*_Hsynch` e pelo .NET): `0 OK`, `1 RTSCountReached`, `2 ParameterError`,
`3 ConnectFailed`, `4 PSBusy`, `5 Inhibited`, `6 StringFound`, `7 TimedOut`, `8 NotAvailable`, `9 SystemError`,
`10 NotSupported`, `11 Unavailable`, `12 SettleTimeReached`. Semântica do `Hsynch` (doc .NET): `bWaitForEvents=true`
espera `nWaitCount` eventos *ReadyToSend*; `false` espera um "settle time" sem `HostScreenChange`; `szVerString`+`Row/Col`
= texto esperado; `nTimeOut=0` → 10.000 ms.

### 4.2 Pro Control Container (`NetManage.ProControlContainer.1`)  [TLB]
**Não licenciado** (pode ser criado diretamente). `CreateSession(SessionTypeConstants)` (0x33; `MFRAMEDisplay=1`),
`Connect()` (0x34), `Disconnect()` (0x39), `TerminateSession(TerminateURL, RemoveSession)` (0x38); acessores
`ObjectXRumbaControl` (1, o próprio display), `ObjectXHostInterface` (5), `ObjectXTelnetInterface` (6),
`ObjectXKeyboardInterface` (7), **`ObjectXHLLAPIInterface` (8)**, `ObjectXMacroInterface` (9), `ObjectXSecurityInterface` (0x5A);
propriedades `IPAddress`, `SourceName`, `HLLAPIShortName` (0x4D), `HostCharacterSet` (0xF, enum `Constants_CharacterSet`),
`SeparateWindow` (0x5D), `ExitOnDisconnect` (0x67), `kc1..kc4`, `DevID`; eventos `AfterConnect`, `HostScreenChange`,
`ReadyToSend`, `KeyboardLock`, `TerminateSession`.

### 4.3 HLLAPI Control (`WallData.HLLAPI.4`) — o lado servidor  [TLB]
Propriedades: `ShortName` (0 = nenhum, 1..26 = A..Z), `ClientConnected`, `ConvertNulls`, `OEMToANSI`, `DDETopic`,
`DDEServer`, `CopyStringTruncate`, `SearchBackPosition`, `KeyPSUpdate`, `StandAlone`, `EHLDLLName`, `WHLDLLName`,
`DeleteSession`, `HllapiStandard`, `HllapiMulSession`. Método `hllapi(Function, Data, Length, Position)`. Seus eventos
(`RequestHostScreenText`, `SendHostKeys`, `RequestHostCursorPosition`, `RequestHostStatusLine`…) são *callbacks* para o
contêiner: é a ponte entre clientes externos da `ehlapi32.Dll` e o display.

### 4.4 Outros modelos de objetos
* **`RumbaScriptAT.System/Session/Screen/Waits`** (`System\ScriptEngine\RumbaScriptAt.dll`): modelo estilo
  Attachmate EXTRA! (`Screen.GetString`, `PutString`, `SendKeys`, `Search`, `WaitForString`, `WaitHostQuiet(5000)`,
  `WaitForCursor`, `OIA`, `Sessions.Open`), construído sobre o motor de scripts do RUMBA (`NMVBHCN.dll` `NMVSI_*`,
  `WDScript.ocx`). Útil como **referência de nomenclatura**; uso externo não validado.
* **.NET RDE** (v10.2.0.0, x86; cópias `*_post2019` são AnyCPU/.NET 4.8):
  `Microfocus.RDE.Framework` (interfaces `IRDEDisplay`, `IMainframeDisplay`, `IRDEConnection.SynchConnect`,
  `IRDESession.SynchSendKeys`, enums `HLLAPIFunctions`, `APIReturnValueConstants`), `RumbaRDEFactory`
  (`RDEFactory.getRDEProvider(RequestType{Desktop, StandAlone}, RDEBaseType{MainframeDisplay…})` — `StandAlone`
  cria um `AxObjectXMFDisplay` próprio num WinForms/STA), `RDEEHLLAPI` (P/Invoke estático para a `ehlapi32.Dll`:
  `WD_*`, `WD_RunProfile`, `WD_GetSessionHWND`, `WD_ShowSession`, structs e 112 constantes `API*` de rc),
  `RDEHLLAPI` (AxHost do controle HLLAPI).

### 4.5 Host Access Web Launcher (`hawlauncher://`)  [IL]
* App **.NET 4.8 WinForms x86** que só hospeda o `WebBrowser` (motor IE/MSHTML) — é por isso que o ActiveX ainda roda
  sem o IE. Sem P/Invoke nem COM próprio.
* Linha de comando: **exatamente 1 argumento contendo `/microfocus_w2h_hawlauncher`**; a URL é o argumento sem o prefixo
  `hawlauncher://` e sem o sufixo. Ex.:
  `"Host Access Web Launcher.exe" "hawlauncher://https://<portal>/rumba694/w2hlegacy/profpro/BRAZIL_D.html/microfocus_w2h_hawlauncher"`.
* `window.external.IsHostAccessWebLauncher()` → `true`; com isso o perfil cria o contêiner **na própria janela**
  (sem popup). Título da janela = título da página (`"BRAZIL_D - Pro Client"`). **Um processo por perfil**
  (validado no L9; §3.1) → o RumbaPy lança o processo e o encerra com `WM_CLOSE` na janela principal (nunca
  `TerminateProcess`). A v2 usa este caminho por padrão (`_launcher.py`).
* Tamanho/posição/**`WindowState`** são salvos por URL (sem query string) em
  `%LOCALAPPDATA%\Micro Focus\Host Access Web Launcher\Host Access Web Launcher.config` (XML, mutex
  `HostAccessWebLauncher_Mutex_SaveSessionSettings`) — dá para pré-configurar a janela minimizada.

---

## 5. Diagnóstico e *trace*

* `RUMBA\System\rumbatrc.ini` configura o *trace* do RUMBA; entradas relevantes: `300 RUMBA Display APIs`
  (`apisvctr.dll`), `400 Host System APIs`, `100 Mainframe Display Data Stream`. Saída: `Display=1`.
* `NMServiceManager\Logs\NMInstall.log` mostra cada abertura de perfil (verificação de componentes).
  O servidor devolve 404 para `/rumba694/w2hlegacy/w2hlegacy/config/*.xml` e o cliente usa as cópias locais — normal.
* Propriedade `HLLAPIDebugFile` (vazia nos perfis) indica suporte a log de depuração do HLLAPI no emulador **[a validar]**.
* Do lado do RumbaPy: `python -m rumbapy doctor` (RUMBA/launcher, Python 32-bit, update, workers vivos, servidores
  0.1.5 nas portas) e os logs em `%LOCALAPPDATA%\rumbapy\logs\` (`client-*.log`, `worker-<tag>-<L>.log`,
  `boot\*.txt`, `update.log`, `pip-*.log`; `RUMBAPY_LOG_LEVEL=DEBUG` para mais detalhe, nunca com texto de tela).

---

## 6. Como reproduzir este levantamento

```bash
python tools/research/pe_exports.py ve "C:/Program Files (x86)/Micro Focus/RUMBA/System/ehlapi32.Dll"
curl -sk https://rumba.srv.volvo.com/rumba694/w2hlegacy/index_pro_api.html
curl -sk https://rumba.srv.volvo.com/rumba694/w2hlegacy/profpro/BRAZIL_D.html
curl -sk https://rumba.srv.volvo.com/rumba694/w2hlegacy/config/procontainerfunc.js
reg query "HKCR\hawlauncher\shell\open\command"
reg query "HKCR\WOW6432Node\CLSID\{913B101A-EDD1-11D5-AD4A-0060B0FB4900}" /s
python tools/research/enum_windows.py <pid do iexplore.exe x86>
%LOCALAPPDATA%\Programs\Python\Python313-32\python.exe tools/research/probe_hllapi.py
python tools/research/dde_list.py
```

# Glossário (mainframe, 3270, RUMBA)

| Termo | Significado |
|---|---|
| **3270** | Família de terminais IBM para mainframe. A tela é um buffer de caracteres (24×80 no modelo 2, 32×80 no 3, 43×80 no 4, 27×132 no 5) enviado em bloco ao host quando se pressiona uma tecla AID. |
| **TN3270 / TN3270E** | 3270 sobre Telnet (TCP, porta 23 por padrão). O "E" adiciona negociação de LU, impressoras e funções. |
| **LU** (Logical Unit) | Identidade do terminal no VTAM. "LU de pool" = o servidor TN3270 escolhe uma livre. |
| **PS** (Presentation Space) | O buffer da tela. No EHLLAPI, posições são lineares e 1-based: `pos = (linha-1)*colunas + coluna`. |
| **OIA** (Operator Information Area) | Linha de status abaixo da tela: indicadores de teclado travado (`X SYSTEM`, `X CLOCK`, `X -f`…), conexão, modo insert. |
| **X SYSTEM / X CLOCK** | Teclado inibido enquanto o host processa. `WD_Wait` espera isso acabar. |
| **AID** (Attention Identifier) | Teclas que enviam a tela ao host: ENTER, PF1–PF24, PA1–PA3, CLEAR, SysReq, Attn. Teclas não-AID (Tab, setas, Erase EOF) só mexem localmente. |
| **Campo / atributo de campo** | A tela formatada é dividida em campos; cada um começa com um byte de atributo (protegido, numérico, oculto, intensificado, MDT). Senhas ficam em campos não exibidos. |
| **MDT** (Modified Data Tag) | Bit que marca o campo como alterado; só campos com MDT são enviados ao host. |
| **EAB** (Extended Attribute Bytes) | Cor, realce etc. por caractere. Com `EAB` ligado, cópias do PS têm 2 bytes por posição. |
| **EBCDIC / CCSID** | Codificação do mainframe. Aqui: **1143** (sueco/finlandês com Euro) nos CICS Brasil, **037** (EUA) no IMS A. O emulador converte para o code page do Windows antes de entregar ao HLLAPI. |
| **HLLAPI / EHLLAPI** | *(Enhanced) High Level Language API* da IBM para programas controlarem um emulador: funções numeradas (1 Connect, 3 SendKey, 8 Copy PS to String…). |
| **WinHLLAPI** | Variante Windows do HLLAPI (`WinHLLAPIStartup`, chamadas assíncronas, *blocking hooks*). No RUMBA: `WHLLAPI.dll`. |
| **`WD_*`** | Funções do RUMBA (prefixo de **W**all **D**ata) que embrulham o EHLLAPI numerado em chamadas C tipadas. |
| **Short name** | Letra A–Z que identifica uma sessão para o HLLAPI. No portal é configurada por número (1=A … 26=Z). |
| **Mnemônico** | Sequência `@x` que representa uma tecla no `SendKey` (`@E` = Enter, `@a` = PF10). `@@` = `@` literal. |
| **W2H / Web-to-Host** | Modalidade do RUMBA em que o emulador é baixado e roda dentro de uma página web (ActiveX). Aqui: "OnWeb Web-to-Host Pro" 6.9.4. |
| **ObjectX** | Família de controles ActiveX do RUMBA (`WdMfDsp.ocx` display, `ProContainer.ocx` contêiner, `WDApiSvc.ocx` HLLAPI). |
| **Perfil** | Página `profpro/<NOME>.html` do portal com toda a configuração da sessão (host, modelo, short name, HLLAPI…). |
| **Host Access Web Launcher** | Aplicativo da Micro Focus (`hawlauncher://`) que abre perfis W2H fora do navegador, hospedando o motor do IE. |
| **CICS** | Monitor transacional da IBM. Transações de 4 letras (ex.: `SPRE`, `SCOM`, `SCPE`) digitadas na linha 1. |
| **IMS / RHELP** | Gerenciador transacional/banco hierárquico da IBM; RHELP é a aplicação usada no IMS A (C1). |
| **USS / USS010** | Tela de logon do VTAM (*Unformatted System Services*) que aparece antes de escolher a aplicação (`IMSRS`). |
| **Modo IE do Edge** | Edge hospedando o motor do IE (`iexplore.exe -dualengineadapter`) para sites legados com ActiveX. |
| **`InternetExplorerMedium`** | Objeto COM `{D5E8041D-…}` que cria um IE em integridade média; é o que o `api.py` usa para abrir o portal. |

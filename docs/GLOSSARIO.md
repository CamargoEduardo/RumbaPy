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
| **`InternetExplorerMedium`** | Objeto COM `{D5E8041D-…}` que cria um IE em integridade média; era o caminho da 0.1.5 para abrir o portal; na v2 só o fallback IE (`_ie_open.js`) o usa. |

## Termos da v2 do RumbaPy

| Termo | Significado |
|---|---|
| **Worker** (processo auxiliar) | Processo Python 32-bit, um por letra e por sessão do Windows, o único que carrega a `ehlapi32.Dll`. Roda a partir dos arquivos do pacote 64-bit (`_worker/`), sem executar `rumbapy/__init__.py`. |
| **BOOT** | Linha única (`_bridge.BOOT`) passada com `-I -S -c` ao Python 32-bit: cria um módulo `rumbapy` vazio apontando para a pasta do pacote e chama `_worker.main`. |
| **Lease** (dono) | Direito exclusivo de uma conexão do pipe de operar o terminal da letra. Nasce no handshake `owner` e termina no fim da conexão (`release()`, fim do processo) ou num `evict`. Conexões `admin` não têm lease. |
| **Evict** | Revogação do lease pelo `close`/`open` de qualquer processo: o worker desconecta a HLLAPI e a `Session` antiga passa a levantar `SessionReplaced`. |
| **`build_id`** | Hash dos arquivos `.py/.js/.json` do pacote; cliente e worker precisam ter o mesmo para conversar como dono (garante a "última versão" também no lado 32-bit). |
| **Heal** | Reparo automático no `import`: qualquer falha de arquivo/sintaxe do pacote reinstala o `master` atual (`_bootstrap.heal`). |
| **`delivery`** | Atributo das exceções de entrada: `none` (nada chegou ao RUMBA), `partial` (parte pode ter chegado), `unknown` (não dá para saber). |
| **`uncertain`** | Estado da `Session` depois de uma entrada com `delivery` `partial`/`unknown`: recusa nova entrada até uma espera de conteúdo dar certo ou `acknowledge()`. |
| **Máscara** | Troca, dentro do worker, do conteúdo de campos não exibidos (senhas) e de spans `secret` por espaços antes de qualquer resposta. `hidden_unknown=True` quando não pôde ser calculada. |
| **Watchdog** | Thread do worker que encerra o processo com `os._exit(3)` se uma chamada da DLL passar do limite (ou se a janela do emulador sumir durante a chamada). |
| **`NORESET`** | Parâmetro de sessão HLLAPI ligado depois do `ConnectPS`: o emulador não manda RESET antes das teclas; teclado bloqueado vira `KeyboardLocked`. |
| **Sandbox** | `RUMBAPY_SANDBOX=1`: o código de produção recusa DLL real, launcher, mensagens a janelas, pip e rede (testes). |
| **Guard** (`GuardedDll`) | Intertravamento dos testes ao vivo (`readonly`, `text`, `login`): recusa, antes da DLL, as funções e teclas fora da política. |
| **Neutralização** | `disconnect` mandado pela v2 a um servidor 0.1.5 ainda vivo, para soltar a conexão HLLAPI da letra (nunca o encerra). |

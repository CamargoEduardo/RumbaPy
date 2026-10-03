# CLAUDE.md — guia para agentes neste repositório

RumbaPy é uma biblioteca Python que automatiza terminais **3270 (CICS/IMS) do Micro Focus RUMBA
Web-to-Host** da Volvo usando a DLL 32-bit `ehlapi32.Dll` (EHLLAPI). O usuário roda scripts em Python
**64-bit**; a DLL só carrega em **32-bit**, então existe um servidor 32-bit separado.
Comece por [`INDEX.md`](INDEX.md).

## Regras invioláveis

1. **O auto-update obrigatório deve ser mantido.** O usuário final tem que rodar sempre a última
   versão (requisito de negócio do dono do projeto). Nunca remova, desligue por padrão ou enfraqueça
   `update_python_32bit` / `update_python_64bit` / `ensure_python_32bit` sem colocar no lugar algo que
   garanta o mesmo — ou mais (ver `docs/PLANO_DE_MELHORIAS.md` §5).
2. **Push no `master` = deploy imediato para todos os usuários.** O auto-update compara o commit
   instalado com `git ls-remote <repo> HEAD`. Não faça commit/push sem pedido explícito; nunca
   empurre código quebrado ou experimental para o `master`.
3. **Compatibilidade da API pública.** Scripts de usuários usam `RumbaClient(terminal_type,
   python32bit_path=None, auto_update=True)` e os métodos `logon_cics`, `logon_rhelp`, `send_key`,
   `copy_ps_to_string`, `copy_string_to_ps`, `copy_field`, `screen_load`, `close_terminal`,
   `disconnect`, `connect`. Não mude nomes/assinaturas/semântica sem camada de compatibilidade.
4. **Credenciais:** nunca commitar, nunca logar (`config.ini` está no `.gitignore`; logon recebe
   `uid`/`pwd` por parâmetro).
5. **O repositório GitHub é PÚBLICO.** Nunca versione dados internos da Volvo (hostnames/IPs de
   mainframe, portas, situação de TLS, chaves `kc1..kc4`/`DevID` dos perfis, telas com dados de negócio).
   Eles ficam em `docs/interno/` (no `.gitignore`); nos docs públicos use apelidos ("host CICS-BR").
6. **A DLL é 32-bit e precisa ser a mesma instalada pelo RUMBA**
   (`C:\Program Files (x86)\Micro Focus\RUMBA\System\ehlapi32.Dll`). A seção compartilhada `.SHARDAT`
   só funciona com o mesmo arquivo. Não copie a DLL para outro lugar.

## Armadilhas que já custaram caro (leia antes de mexer em `api.py`/`server.py`)

* A conexão HLLAPI é **por processo**, não por objeto nem por `hwnd` (`docs/RUMBA_EHLLAPI.md` §4).
* Chamadas à DLL podem **bloquear para sempre** (`WaitForSingleObject(INFINITE)` dentro da DLL).
  Threads abandonadas continuam presas; a saída robusta é reciclar o processo com `os._exit()`.
  **Nunca** `TerminateProcess`/`taskkill /F` no processo que carregou a DLL (vaza a tabela global de
  32 clientes → popup modal "Exceeded maximum number of clients, reboot for correction").
* Não crie uma thread por chamada se `MultiThreadConnectionOn()` estiver ligado (cada thread gasta um slot).
* Texto da DLL é **ANSI/OEM do Windows** (provável `cp1252`), não UTF-8. `buffer.value` corta no
  primeiro `NUL`; use `.raw[:n]`. Ver `docs/KNOWN_ISSUES.md` §2.
* `WD_SendKey` rejeita ≥ 256 bytes (rc=2); o manual recomenda ≤ 128. **Tudo após a 1ª tecla AID
  (Enter/PFn/PAn/Clear) é descartado** — uma AID por chamada. `@` literal é `@@`.
* `WD_Wait` == 0 só diz que o teclado aceita digitação, **não** que a próxima tela chegou: espere por texto/cursor.
* Chame `WD_ResetSystem` antes de encerrar o processo que usa a DLL.
* IMS A tem **32×80** (modelo 3); BRAZIL D/Z têm 24×80. A fórmula `pos=(y-1)*80+x` assume 80 colunas.
* Só existem sessões HLLAPI se o terminal foi aberto por um perfil de `index_pro_api.html`
  (o atalho do usuário abre `index_pro.html`, **sem** HLLAPI).
* `close_terminal` hoje fecha janelas por regex de título — pode fechar coisas do usuário.
* O servidor 32-bit é persistente e **não** é trocado após um update (bug 1.1 de `KNOWN_ISSUES.md`).

## Layout

```
src/rumbapy/__init__.py   exporta RumbaClient
src/rumbapy/client.py     lado 64-bit: auto-update, descoberta do Python 32-bit, cliente TCP JSON
src/rumbapy/server.py     lado 32-bit: servidor TCP persistente, logon_cics/logon_rhelp/screen_load
src/rumbapy/api.py        lado 32-bit: ctypes → ehlapi32.Dll, abertura via IE COM, fechamento via pywinauto
tools/research/           ferramentas de engenharia reversa/sondagem (não empacotadas)
docs/                     referência técnica, problemas conhecidos, plano
```

## Ambiente de desenvolvimento

* Windows com RUMBA instalado pelo portal (`https://rumba.srv.volvo.com/rumba694/w2hlegacy/`).
* Dois Pythons: 64-bit (usuário) e 32-bit ≥ 3.13 (servidor), normalmente em
  `%LOCALAPPDATA%\Programs\Python\Python313\` e `...\Python313-32\`. `py -0p` lista os instalados.
* O lado 32-bit precisa de `pywin32` e `pywinauto` (instalados pelo próprio auto-update).
* Não há testes automatizados. Sondagem sem efeitos colaterais:
  `%LOCALAPPDATA%\Programs\Python\Python313-32\python.exe tools\research\probe_hllapi.py`.
* Testar contra o mainframe abre sessões reais de produção: peça autorização ao usuário antes de
  conectar, digitar ou fazer logon. Nunca use credenciais reais em arquivos.
* Para desenvolver sem acionar o auto-update use `RumbaClient(..., auto_update=False)` e, se mexer no
  lado 32-bit, encerre o servidor antigo (ele fica escutando em `localhost:8500/8600/8700`).

## Convenções do código existente

* Identificadores em inglês; mensagens, logs e comentários em **português**.
* `logger = logging.getLogger(__name__)` por módulo; sem `print`.
* Imports: primeiro `from x import y`, depois `import x`, **cada bloco ordenado por comprimento
  decrescente da linha** (ex.: `import subprocess` antes de `import json`).
* O servidor responde `{'success': bool, 'error': str, ...}`; o cliente transforma `success=False`
  em `RuntimeError`.
* Coordenadas públicas são `(y, x)` = (linha, coluna), 1-based.
* Versão em `pyproject.toml` é incrementada a cada mudança publicada.

## Documentação

| Arquivo | Conteúdo |
|---|---|
| `INDEX.md` | Mapa de tudo (comece aqui) |
| `ARCHITECTURE.md` | Arquitetura atual, fluxos e por que é assim |
| `docs/RUMBA_EHLLAPI.md` | Referência da `ehlapi32.Dll`: 58 exports, assinaturas, IPC, threads, rc, mnemônicos |
| `docs/RUMBA_AMBIENTE.md` | Instalação RUMBA, perfis do portal, hosts, COM, janelas, caminhos de automação |
| `docs/KNOWN_ISSUES.md` | Bugs e riscos com `arquivo:linha` e correção sugerida |
| `docs/PLANO_DE_MELHORIAS.md` | Arquitetura-alvo, ADRs, fases, decisões pendentes |
| `tools/research/README.md` | Como reproduzir a engenharia reversa |
| `docs/GLOSSARIO.md` | Jargão de mainframe/3270/RUMBA |
| `docs/interno/` (não versionado) | Hosts reais; `referencia/tlb` e `referencia/dotnet` (dumps completos de type libraries e APIs .NET); `referencia/portal` (páginas do portal); `referencia/fontes` (**manual "WD_API Language Reference"** em PDF/TXT, guias Rocket/Blue Prism, relatório da pesquisa web); notas brutas de RE |

Mantenha esses arquivos atualizados quando mudar comportamento: são a memória do projeto.

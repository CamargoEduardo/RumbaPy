# Como voltar para a versão anterior (rollback)

Este documento existe desde o merge da v2.0.0 no `master` (2026-10-04). Ele diz onde está o código antigo e como
voltar a ele — no repositório e nas máquinas dos usuários.

## Referências salvas

| Referência | Aponta para | Para que serve |
|---|---|---|
| branch `legacy/v0.1.5` | `5c03d47` — o `master` imediatamente antes da v2 (versão **0.1.5**) | trabalhar/consultar o código antigo (`git checkout legacy/v0.1.5`) |
| tag `v0.1.5` | `5c03d47` (o mesmo commit) | referência fixa e imutável da 0.1.5 (`git show v0.1.5:src/rumbapy/client.py`) |
| tag `v2.0.0` | o **commit de merge** do `dev` no `master` | ponto exato da troca; é o commit que se reverte para voltar |
| `master` | merge `--no-ff` do `dev` (todo o histórico da v2 preservado) | — |

Conferir a qualquer momento: `git log --oneline -1 v0.1.5`, `git log --oneline -1 v2.0.0`,
`git branch -a --contains v0.1.5`.

## Antes de tudo: como o auto-update afeta o rollback

* Todo usuário roda o commit do `master` **publicado no GitHub** (a v2 lê `refs/heads/master`; a 0.1.5 usava
  `git ls-remote <repo> HEAD`). Por isso o rollback é feito **no `master`**: quando o `master` publicado voltar a ter o
  código antigo, cada instalação se atualiza sozinha para ele na próxima execução.
* Não existe rollback "só para um usuário" enquanto o `master` for v2: uma 0.1.5 reinstalada à mão se atualizaria de
  novo para o `master` no primeiro uso. Isso é o auto-update obrigatório funcionando.
* Validado com pip real (2026-10-04): uma instalação 2.0.0 aceita a reinstalação do zip do commit `5c03d47` e passa a
  importar a 0.1.5 (`rumbapy.RumbaClient` vindo de `rumbapy.client`). A 0.1.5 é construída com `setuptools` (o pip o
  baixa sozinho no build isolado) e, no primeiro uso, reinstala `pywin32`/`pywinauto` no Python 32-bit, como sempre fez.

## Caso 1 — o merge ainda NÃO foi publicado (só local)

Nada chegou aos usuários. Para desfazer o merge local:

```
git checkout master
git reset --hard v0.1.5
```

## Caso 2 — já publicado: reverter o merge (recomendado; não reescreve o histórico)

```
git checkout master
git pull
git revert -m 1 v2.0.0 --no-edit      # commit NOVO cujo conteúdo é exatamente o da 0.1.5
git diff v0.1.5 master --stat         # tem de sair vazio
git push origin master
```

O que acontece com os usuários depois do push:

1. Script que importa a v2: o auto-update vê o commit novo, reinstala o zip dele (código 0.1.5) e para com
   `UpdateRequired` ("Rode o script de novo."). Na execução seguinte já roda a 0.1.5.
2. Jupyter com a v2 na memória: reinicie o kernel.
3. Workers 32-bit da v2 que estiverem vivos saem sozinhos (ociosidade de 30 min ou fim do lease); não atrapalham a 0.1.5,
   que usa outras portas/processos. Para liberar na hora: feche os terminais (`X`) e os scripts.

Para **voltar para a v2 depois** do rollback: `git revert <sha do commit de revert> --no-edit` e `git push origin master`
(o "revert do revert" restaura a v2 inteira).

## Caso 3 — alternativa: mover o `master` de volta (reescreve o histórico público)

Só se o Caso 2 for impossível. O efeito para os usuários é o mesmo (o auto-update instala o commit antigo), mas a
história do `master` publicada é reescrita e quem tiver clonado o repositório precisa reconfigurar:

```
git push --force-with-lease origin v0.1.5:master
```

## Problemas na transição para a v2 (sem precisar de rollback)

| Sintoma | O que fazer |
|---|---|
| Script 0.1.5 aberto durante a publicação recebe "O RumbaPy foi atualizado para a v2. Rode o script de novo." | rodar o script de novo (o processo ainda tinha a 0.1.5 na memória) |
| "O RumbaPy foi reparado…" / "rode de novo" na 1ª execução depois de instalar pelo README | normal: a 1ª execução reinstala com o commit exato; rode de novo |
| Instalação quebrada (`ImportError`, arquivos faltando) | `python -m pip install --force-reinstall --no-deps https://github.com/CamargoEduardo/RumbaPy/archive/refs/heads/master.zip` |
| Diagnóstico | `python -m rumbapy doctor` (não mexe no terminal) |
| Servidor 0.1.5 antigo ainda em memória (portas 8500/8600/8700/8800) | a v2 o desconecta do terminal sozinha; ele some no logoff |

## Código antigo para consulta

* `git checkout legacy/v0.1.5` (lembre de voltar: `git checkout dev`).
* Um arquivo só: `git show v0.1.5:src/rumbapy/server.py`.
* Zip (depois que a tag for publicada): `https://github.com/CamargoEduardo/RumbaPy/archive/refs/tags/v0.1.5.zip`.

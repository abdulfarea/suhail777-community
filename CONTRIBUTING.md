# Contribuindo

Obrigado por ajudar o SUHAIL777DEFENSE. O repositorio e publico e aceita correcoes pequenas, testes, documentacao e propostas que mantenham o foco em leitura cautelosa e colaboracao verificavel.

## Antes de comecar

1. Leia o `README.md`, `SECURITY.md` e o `CODE_OF_CONDUCT.md`.
2. Procure uma issue existente antes de abrir outra.
3. Nao inclua credenciais, chaves privadas, tokens reais, saldos, PII, nomes privados ou dados de usuario.
4. Use fixtures e valores ficticios nos exemplos. Nao envie capturas de tela de carteiras.

## Branch e pull request

Crie uma branch descritiva a partir de `main`, por exemplo `fix/validacao-mint` ou `docs/quickstart`. Mantenha cada PR focado, explique o motivo da mudanca e liste os testes executados.

Um PR deve:

- manter Python 3.9+ e a biblioteca padrao quando possivel;
- preservar o comportamento read-only e fail-closed;
- nao assinar transacoes, movimentar fundos, guardar chaves ou criar um bot 24 horas;
- evitar alegacoes de seguranca, fraude, retorno financeiro, bots, reembolsos ou lei;
- incluir testes para comportamento novo e atualizar a documentacao quando necessario;
- nao adicionar segredos, dependencias desnecessarias ou chamadas de rede nos testes.

O template de PR ajuda a revisar esses pontos. O CI roda nos pushes da `main`, pull requests e execucao manual. Rode localmente:

```bash
python3 -m unittest discover -s tests -v
```

Nao ha garantia de prazo de revisao, manutencao continua ou resposta em tempo real. Nao presuma que existe um bot ativo acompanhando issues ou carteiras. Uma contribuicao respeitosa pode ser fechada ou pedir ajustes sem promessa de aprovacao.

## Issues e revisao

Use os templates para bugs e funcionalidades. Descreva passos reproduziveis, ambiente e saida sanitizada. Nao publique informacao sensivel: consulte `SECURITY.md` para vulnerabilidades.

A revisao deve discutir codigo, testes, documentacao e impacto read-only. Nao use ataques pessoais, insultos, opinioes sobre pessoas ou alegacoes nao verificadas como argumento tecnico.

## Licenca

Ao enviar uma contribuicao, voce concorda que ela pode ser distribuida sob a licenca MIT deste repositorio. Isso nao concede exclusividade sobre a marca SUHAIL777DEFENSE nem sobre o logo.

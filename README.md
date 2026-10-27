<p align="center">
  <img src="assets/cover.png" alt="SUHAIL777DEFENSE">
</p>

<h1 align="center">SUHAIL777DEFENSE</h1>

<p align="center">Ferramentas Python read-only para inspecao cautelosa de dados publicos e fixtures locais.</p>

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MIT License](https://img.shields.io/badge/License-MIT-750014)](LICENSE)
[![Read-only](https://img.shields.io/badge/mode-read--only-2E8B57)](#limites-e-seguranca)
[![CI](https://github.com/abdulfarea/suhail777-community/actions/workflows/tests.yml/badge.svg)](https://github.com/abdulfarea/suhail777-community/actions/workflows/tests.yml)

> Utilidade pratica para revisar sinais tecnicos sem prometer diagnostico, seguranca ou retorno financeiro. Este projeto nao substitui pesquisa propria nem aconselhamento financeiro.

## Sobre o projeto

O SUHAIL777DEFENSE e um repositorio publico para colaboracao externa em ferramentas pequenas, transparentes e executaveis com Python 3.9+. O foco e inspecao local e somente leitura: entradas de exemplo, classificacao conservadora e verificacoes que fecham com cautela quando algo e desconhecido.

O projeto publico contem codigo e dados de fixtures. Nao inclui dados de usuario, carteiras reais, saldos, capturas de tela, nomes privados ou vinculos para repositorios privados.

## Inicio rapido

Nao ha instalacao de pacote: use Python 3.9 ou superior e clone o repositorio publico.

```bash
git clone https://github.com/abdulfarea/suhail777-community.git
cd suhail777-community

# Inspecao local de um mint de exemplo (Fartcoin)
python3 defense.py inspect 9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump

# Filtragem do fixture de exemplo
python3 defense.py filter --input tokens.example.json

# Carteira somente leitura: informe a sua em uma sessao local, sem gravar no repositorio
read -r -p "Endereco publico Solana: " SOLANA_WALLET
export SOLANA_WALLET
python3 defense.py positions
unset SOLANA_WALLET

# Verificacao canary do mesmo mint de exemplo
python3 canary.py 9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump
```

O comando `positions` tambem aceita `--wallet ENDERECO`, mas a variavel de ambiente sem argumento e preferida para reduzir exposicao no historico e na lista de processos. A saida contem dados financeiros: nao a publique em issues. Nao cole chaves privadas. `SOLANA_RPC_URL` e `JUPITER_QUOTE_URL` aceitam endpoints HTTPS opcionais. `JUPITER_API_KEY` e opcional e deve ficar somente no ambiente local, nunca no repositorio. Nenhuma API key real e fornecida aqui. O mint Fartcoin e apenas um exemplo tecnico, nao uma recomendacao de compra.

## O que esta entregue

| Capacidade | Entregue | Limite intencional |
| --- | --- | --- |
| `inspect MINT` | Leitura e classificacao de sinais do mint | Nao e veredito de scam, seguranca ou autenticidade |
| `filter --input` | Filtragem local de `tokens.example.json` | Nao bloqueia ativos nem consulta uma lista magica |
| `positions` | Leitura de posicoes de uma carteira informada | Nao assina, transfere, compra, vende ou guarda fundos |
| Classificacao | `CRITERIOS_ATENDIDOS`, `REVISAR` e `INCONCLUSIVO` | Criterios sao limitados; resultado nao significa seguro |
| Falha segura | Resultado conservador para campos ausentes ou desconhecidos | `unknown` nao significa `scam` |
| Token program | Sinaliza Token-2022 e extensoes desconhecidas para revisao | Nao presume que uma extensao desconhecida e segura |
| Canary | Checagem curta e explicita por mint | Nao e monitor 24 horas nem bot de carteira |
| Testes e CI | Testes locais e CI para pushes na `main` e pull requests | Nao ha promessa de operacao continua ou resposta do maintainer |

O modo de carteira e read-only e nao tem armazenamento de chave privada, transacao de fundos ou daemon de 24 horas. O projeto nao promete detectar todos os riscos, provar autoria, validar uma oportunidade ou impedir perdas.

## Limites e seguranca

Os estados sao deliberadamente modestos:

- `CRITERIOS_ATENDIDOS`: os criterios implementados foram observados; isso nao quer dizer seguro.
- `REVISAR`: ha um sinal que merece verificacao humana ou evidencia adicional.
- `INCONCLUSIVO`: faltam dados, ha erro de leitura ou existe uma extensao que o codigo nao conhece.

A logica e fail-closed: ausencia de evidencia nao e evidencia de seguranca. Um retorno `unknown` nao e acusacao de fraude e nao deve ser apresentado como `scam`.

## Desenvolvimento

A suite usa apenas a biblioteca padrao do Python:

```bash
python3 -m unittest discover -s tests -v
```

O CI nativo roda em Python 3.9 e 3.12 para pushes na `main`, pull requests e execucao manual. Os testes nao devem acessar a rede, pedir segredos ou operar carteiras; o workflow nao e um bot de carteira 24 horas. Nao existe garantia de tempo de resposta ou de manutencao continua.

## Roadmap

Estes itens sao ideias de trabalho, nao funcionalidades ja operacionais:

1. Adicionar hooks de avisos locais e opt-in, sem transformar o projeto em monitor continuo.
2. Estudar integracao de identidade e evidence com escopo minimo, consentimento e modo somente leitura.
3. Melhorar relatorios reproduziveis para auditoria de fixtures e revisao humana.

## Como colaborar

Ideias pequenas, correcoes e testes sao bem-vindos. Antes de abrir uma mudanca, leia [CONTRIBUTING.md](CONTRIBUTING.md), rode a suite e mantenha a alteracao focada.

- [Abrir uma issue](https://github.com/abdulfarea/suhail777-community/issues/new/choose)
- [Abrir um pull request](https://github.com/abdulfarea/suhail777-community/compare)
- [Participar das discussoes](https://github.com/abdulfarea/suhail777-community/discussions)
- [Relatar vulnerabilidade de forma privada](https://github.com/abdulfarea/suhail777-community/security)
- [Consultar seguranca](SECURITY.md)

Ao relatar um bug, remova credenciais, chaves, enderecos privados, PII, saldos e qualquer dado de usuario. Use apenas fixtures ou valores claramente ficticios. A colaboracao deve melhorar a funcionalidade e a legibilidade, nao fazer alegacoes sobre bots, reembolsos ou leis.

## Sustentabilidade e servicos

O codigo aberto pode apoiar servicos de instalacao, treinamento e desenvolvimento de integracoes somente leitura. Veja [possibilidades de monetizacao](docs/MONETIZACAO.md). Nenhum pagamento, assinatura, patrocinio ou canal de cobranca esta ativado; receitas dependem de clientes, entrega e manutencao, nao sao garantidas.

## Licenca e uso

O codigo e distribuido sob a [licenca MIT](LICENSE), conforme o arquivo de licenca que acompanha o repositorio. A licenca permite copiar e usar comercialmente o codigo dentro dos seus termos; a marca SUHAIL777DEFENSE e o logo nao sao uma concessao exclusiva por este repositorio. Nao ha promessa de auditoria legal, financeira ou de seguranca.

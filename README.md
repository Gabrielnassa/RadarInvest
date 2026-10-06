# Radar de Investimentos

Triagem de ações da B3 e criptomoedas. Coleta dados públicos, calcula uma nota de 0 a 100 por ativo com base em métodos conhecidos e mostra um ranking com o motivo de cada nota.

**Painel:** https://gabrielnassa.github.io/RadarInvest/

A nota mostra se o ativo passa nos critérios de cada método. Não é previsão de preço nem recomendação de investimento, e as regras ainda não foram testadas contra o passado.

## Situação do projeto

| Etapa | O que faz | Situação |
|---|---|---|
| 1. Coleta e banco | Baixa os dados e grava em SQLite | Pronta e rodando com dados reais |
| 2. Notas | Calcula a nota de cada método | Pronta |
| 3. Backtest | Verifica se a nota teria funcionado no passado | A fazer |
| 4. IA | Explica cada nota em texto livre | A fazer. Hoje o texto é montado por regra fixa |
| 5. Painel | Tela com ranking, detalhe e carteira | Pronto, lendo dados reais |

## Como funciona

1. De terça a sábado, às 06h15 de Brasília, a rotina `.github/workflows/atualizar.yml` roda nos servidores do GitHub.
2. Ela executa a coleta (`python -m radar coletar`) e o cálculo das notas (`python -m radar exportar`).
3. O resultado vai para `docs/dados.json`, que o painel em `docs/index.html` lê.
4. O registro da última coleta fica em `docs/coleta.log`.

Para rodar fora de hora: aba Actions do repositório, rotina "Atualizar dados", botão "Run workflow".

## Métodos e regras

| Método | Regra | Nota |
|---|---|---|
| Barsi | Preço-teto = dividendo típico por ação (mediana de 5 anos) / 6% | 50 no teto, 100 com 50% de margem; +10 nos setores de bancos, energia, saneamento, seguros e telecom; -20 se pagou em menos de 4 dos 5 anos |
| Bazin | Dividendos sobre o preço, pelo menor entre os 12 meses e o ano típico | 60 com 6%, 100 com 10%; reduz se não pagou em todos os 5 anos ou se a dívida passa do patrimônio |
| Qualidade | Checklist de 8 itens: retorno sobre o patrimônio, lucro recorrente, crescimento, dívida, histórico e liquidez | Percentual de itens atendidos |
| Graham | Valor = raiz(22,5 x lucro por ação x patrimônio por ação) | 50 no valor, 100 com 50% de margem; zero com prejuízo. Se o lucro de 12 meses passa do dobro da média de 3 anos, usa a média |
| Greenblatt | Ranking por lucro operacional sobre o valor da firma e sobre o capital | 100 para a melhor colocada, 10 para a última; não se aplica a bancos e seguradoras |

A nota final é a média ponderada dos métodos que se aplicam (pesos no `config.yaml`). Entram no ranking as ações com volume médio acima de R$ 500 mil por dia, uma por empresa (a mais negociada).

Cripto usa regras próprias: tendência em relação à média de 200 dias (45%), volatilidade de 30 dias (30%) e tamanho (25%).

Todas as regras ficam em `radar/notas.py` e os limites na seção `notas` do `config.yaml`.

## Fontes de dados

| Fonte | O que traz | Observação |
|---|---|---|
| B3 (arquivo COTAHIST) | Cotação diária de ações, units, BDRs e fundos imobiliários | Oficial. Preços sem ajuste por proventos |
| CVM (dados abertos) | Cadastro, balanço, resultado, fluxo de caixa e quantidade de ações | Oficial |
| Yahoo Finance | Dividendos e JCP por ação | Não oficial. Pode incluir pagamentos extraordinários e pode mudar ou bloquear |
| CoinGecko | 30 maiores criptomoedas, um ano de histórico | Gratuita, com limite de chamadas |
| Banco Central (SGS) | Selic, IPCA, dólar e CDI | Oficial |

## Rodar no seu computador (Windows)

1. Instale o Python 3.11 ou mais novo, marcando "Add python.exe to PATH".
2. Dê dois cliques em `iniciar.bat`.

Ele prepara o ambiente, coleta, calcula as notas e abre o painel no navegador. A primeira coleta demora; as seguintes só buscam o que mudou.

- `diagnostico.bat` testa o acesso a cada fonte.
- `testar.bat` roda os testes automatizados.

Linha de comando:

```
python -m radar coletar                  coleta tudo
python -m radar coletar --fonte cripto   coleta só uma fonte (b3, cvm, dividendos, cripto, macro)
python -m radar exportar                 calcula as notas e grava docs/dados.json
python -m radar painel                   abre o painel neste computador
python -m radar status                   mostra o que há no banco
python -m radar diagnostico              testa as fontes
python -m unittest discover -s tests -t .
```

## O que foi conferido e o que não foi

Conferido:

- A coleta roda contra as cinco fontes reais nos servidores do GitHub.
- Preço sobre o lucro, preço sobre o patrimônio, retorno sobre o patrimônio, dividendos e dívida de Sanepar, BB Seguridade e Cemig batem com os valores publicados em sites de mercado na mesma data.
- Os testes automatizados cobrem a leitura de cada fonte e as fórmulas das notas, com casos calculados à mão.

Não conferido:

- Se as notas têm algum poder de indicar bons investimentos. Isso depende do backtest, que ainda não existe.
- Os arquivos `.bat` em Windows.
- Empresa por empresa. Planos de contas fora do padrão podem gerar números errados em casos isolados.

## Limitações conhecidas

- Dividendos vêm do Yahoo e podem incluir pagamentos extraordinários ou reduções de capital. O painel alerta quando os dividendos de 12 meses passam de 15% do preço.
- A dívida líquida é comparada com o lucro operacional, e não com o Ebitda.
- Preços antigos e quantidade de ações são ajustados por desdobramentos informados pelo Yahoo. Um erro nessa fonte distorce a variação de 12 meses.
- Fundos imobiliários têm cotação coletada, mas ainda não recebem nota.
- Ações cujo código de negociação não aparece no cadastro da CVM ficam fora do ranking. A lista aparece na aba Coleta.

## Estrutura

```
radar/                 código
  __main__.py          linha de comando
  db.py                esquema do banco
  rede.py              download, cache e novas tentativas
  notas.py             indicadores e notas por método
  exportar.py          gera docs/dados.json
  fontes/              um módulo por fonte
tests/                 testes automatizados
docs/index.html        painel
docs/dados.json        dados do painel (gerado pela rotina)
config.yaml            configuração
```

## Aviso

Conteúdo informativo. Não é recomendação de compra ou venda. Oferecer recomendação de investimento ao público é atividade regulada pela CVM.

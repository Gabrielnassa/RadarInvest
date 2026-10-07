# Radar de Investimentos

Triagem de ações e fundos imobiliários da B3, Tesouro Direto e criptomoedas. Coleta dados públicos, calcula uma nota de 0 a 100 por ativo com base em métodos conhecidos e mostra um ranking com o motivo de cada nota.

**Painel:** https://gabrielnassa.github.io/RadarInvest/

A nota mostra se o ativo passa nos critérios de cada método. Não é previsão de preço nem recomendação de investimento. O teste das regras no passado fica na aba Desempenho, com os limites dele.

## Situação do projeto

| Etapa | O que faz | Situação |
|---|---|---|
| 1. Coleta e banco | Baixa os dados e grava em SQLite | Pronta e rodando com dados reais |
| 2. Notas | Calcula a nota de cada método | Pronta |
| 3. Backtest | Verifica se a nota teria funcionado no passado | Pronto (`radar/backtest.py`), com período curto |
| 4. IA | Explica cada nota em texto livre | A fazer. Hoje o texto é montado por regra fixa |
| 5. Painel | Ranking, detalhe, FIIs, renda fixa, comparação, carteira, simulador e desempenho | Pronto, lendo dados reais |

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
| Qualidade | Checklist de 8 itens: retorno sobre o patrimônio, lucro recorrente, crescimento, dívida sobre Ebitda, histórico e liquidez | Percentual de itens atendidos |
| Graham | Valor = raiz(22,5 x lucro por ação x patrimônio por ação) | 50 no valor, 100 com 50% de margem; zero com prejuízo. Se o lucro de 12 meses passa do dobro da média de 3 anos, usa a média |
| Greenblatt | Ranking por lucro operacional sobre o valor da firma e sobre o capital | 100 para a melhor colocada, 10 para a última; não se aplica a bancos e seguradoras |

A nota final é a média ponderada dos métodos que se aplicam (pesos no `config.yaml`). Entram no ranking as ações com volume médio acima de R$ 500 mil por dia, uma por empresa (a mais negociada).

A aba **Longo prazo** tem uma nota separada, que não entra na nota final: percentual do peso atendido num checklist de lucro em todos os 5 anos (20), lucro em todos os trimestres (10), lucro maior que há 5 anos (15), retorno sobre o patrimônio de 15% ou mais (15), dívida líquida de até 3 vezes o Ebitda (10), receita crescendo 5% ao ano (5), dividendos em todos os 5 anos (15), preço sobre o lucro entre 0 e 15 (10) e setor perene (5). Prejuízo em 12 meses ou patrimônio negativo limitam a nota a 30.

Fundos imobiliários (volume acima de R$ 300 mil por dia): dividendos de 12 meses (35%; 6% vale 0 e 12% vale 100), preço sobre o valor patrimonial da cota (25%; 1,0 vale 50 e 0,8 vale 100), meses com pagamento nos últimos 12 (25%) e liquidez (15%; R$ 2 milhões por dia vale 100). O valor patrimonial e o segmento vêm do informe mensal da CVM, ligado ao código da B3 pelo ISIN.

**Backtest.** Em maio e novembro de cada ano, as notas são recalculadas como se fosse aquele dia: só cotações e dividendos até a data e só balanços com 90 dias ou mais. Depois mede o retorno de 12 meses, somando dividendos, das 10 maiores notas finais, das 10 maiores notas de longo prazo, de todas as ações do ranking e do CDI. Só entram empresas que ainda estão no cadastro da CVM, o que favorece o resultado.

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
| CVM (informe mensal de FII) | Patrimônio, valor da cota, segmento e cotistas dos fundos imobiliários | Oficial |
| Tesouro Transparente | Taxas e preços do Tesouro Direto | Oficial |

## Rodar no seu computador (Windows)

1. Instale o Python 3.11 ou mais novo, marcando "Add python.exe to PATH".
2. Dê dois cliques em `iniciar.bat`.

Ele prepara o ambiente, coleta, calcula as notas e abre o painel no navegador. A primeira coleta demora; as seguintes só buscam o que mudou.

- `diagnostico.bat` testa o acesso a cada fonte.
- `testar.bat` roda os testes automatizados.

Linha de comando:

```
python -m radar coletar                  coleta tudo
python -m radar coletar --fonte cripto   coleta só uma fonte (b3, cvm, dividendos, cripto, macro, fii, tesouro)
python -m radar exportar                 calcula as notas, roda o backtest e grava docs/dados.json
python -m radar painel                   abre o painel neste computador
python -m radar status                   mostra o que há no banco
python -m radar diagnostico              testa as fontes
python -m unittest discover -s tests -t .
```

## O que foi conferido e o que não foi

Conferido:

- A coleta roda contra as cinco fontes reais nos servidores do GitHub.
- Preço sobre o lucro, preço sobre o patrimônio, retorno sobre o patrimônio e dividendos de Sanepar e BB Seguridade batem com os valores de um site de mercado na mesma data. Na Cemig o retorno bate e o preço sobre o lucro ficou cerca de 6% acima do site.
- Os testes automatizados cobrem a leitura de cada fonte e as fórmulas das notas, com casos calculados à mão.

Não conferido:

- Se as notas têm poder de indicar bons investimentos. O backtest existe, mas cobre poucos anos e ignora empresas que saíram da bolsa.
- Os coletores de FII e do Tesouro foram escritos a partir do formato publicado e testados com arquivos de exemplo; a primeira coleta real confirma.
- Os arquivos `.bat` em Windows.
- Empresa por empresa. Planos de contas fora do padrão podem gerar números errados em casos isolados.

## Limitações conhecidas

- Dividendos vêm do Yahoo e podem incluir pagamentos extraordinários ou reduções de capital. O painel alerta quando os dividendos de 12 meses passam de 15% do preço e quando a soma dos dois últimos anos difere em mais de 50% do caixa pago que a empresa informou à CVM.
- A dívida líquida é comparada com o Ebitda (lucro operacional mais depreciação do fluxo de caixa). Sem a depreciação, usa o lucro operacional.
- Preços antigos e quantidade de ações são ajustados por desdobramentos informados pelo Yahoo. Um erro nessa fonte distorce a variação de 12 meses.
- Ações cujo código de negociação não aparece no cadastro da CVM ficam fora do ranking. A lista aparece na aba Coleta.

## Estrutura

```
radar/                 código
  __main__.py          linha de comando
  db.py                esquema do banco
  rede.py              download, cache e novas tentativas
  notas.py             indicadores e notas por método
  backtest.py          teste das notas no passado
  exportar.py          gera docs/dados.json
  fontes/              um módulo por fonte
tests/                 testes automatizados
docs/index.html        painel
docs/dados.json        dados do painel (gerado pela rotina)
config.yaml            configuração
```

## Aviso

Conteúdo informativo. Não é recomendação de compra ou venda. Oferecer recomendação de investimento ao público é atividade regulada pela CVM.

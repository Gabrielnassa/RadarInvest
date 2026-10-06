# Radar de Investimentos

Sistema de triagem de ações da B3 e criptomoedas. Coleta dados públicos, calcula uma nota por ativo com base em métodos conhecidos (Barsi, Bazin, checklist de qualidade, Graham, Greenblatt) e mostra um ranking com a explicação de cada nota.

A nota indica se o ativo passa nos critérios dos métodos. Não é previsão de preço nem recomendação de investimento.

## Situação do projeto

| Etapa | O que faz | Situação |
|---|---|---|
| 1. Coleta e banco | Baixa os dados e grava em SQLite | Pronta, falta validar contra as fontes reais |
| 2. Notas | Calcula a nota de cada método | A fazer |
| 3. Backtest | Verifica se a nota teria funcionado no passado | A fazer |
| 4. IA | Explica cada nota em texto | A fazer |
| 5. Painel | Tela com ranking, detalhe e carteira | Prévia em `docs/index.html`, com dados fictícios |

## Como usar (Windows)

1. Instale o Python 3.11 ou mais novo, marcando "Add python.exe to PATH".
2. Dê dois cliques em `iniciar.bat`.

Na primeira vez ele prepara o ambiente, baixa vários anos de histórico e mostra um resumo do que entrou no banco. A primeira coleta pode demorar bastante; as seguintes só buscam o que mudou.

- `diagnostico.bat` testa o acesso a cada fonte e mostra qual falhou.
- `testar.bat` roda os testes automatizados.

Linha de comando:

```
python -m radar coletar                  coleta tudo
python -m radar coletar --fonte cripto   coleta só uma fonte (b3, cvm, dividendos, cripto, macro)
python -m radar status                   mostra o que há no banco
python -m radar diagnostico              testa as fontes
python -m unittest discover -s tests -t .
```

Em Linux ou macOS: crie um ambiente virtual, rode `pip install -r requirements.txt` e use os mesmos comandos.

## Fontes de dados

| Fonte | O que traz | Observação |
|---|---|---|
| B3 (arquivo COTAHIST) | Cotação diária de ações, units, BDRs e fundos imobiliários | Oficial. Preços sem ajuste por proventos |
| CVM (dados abertos) | Cadastro das empresas, balanço, resultado e fluxo de caixa | Oficial |
| Yahoo Finance | Dividendos e JCP por ação | Não oficial. Pode mudar ou bloquear |
| CoinGecko | 30 maiores criptomoedas, um ano de histórico | Gratuita, com limite de chamadas |
| Banco Central (SGS) | Selic, IPCA, dólar e CDI | Oficial |

Anos de histórico, quantidade de criptos, séries do Banco Central e liquidez mínima ficam no `config.yaml`.

## O que fica no banco

O banco é criado em `dados/radar.db` (a pasta `dados/` não vai para o repositório).

| Tabela | Conteúdo |
|---|---|
| `ativos` | Um registro por código da B3, com tipo e CNPJ da empresa |
| `cotacoes` | Abertura, máxima, mínima, fechamento e volume por dia |
| `empresas` | CNPJ, código CVM, nome e setor |
| `empresa_tickers` | Códigos de negociação de cada empresa |
| `demonstrativos` | Contas do balanço, resultado e fluxo de caixa, em reais |
| `dividendos` | Valor por ação e data, com a fonte |
| `cripto_ativos` | Posição, preço, valor de mercado e distância da máxima |
| `cripto_cotacoes` | Preço diário |
| `macro` | Séries do Banco Central (a visão `macro_atual` dá o último valor) |
| `coletas` | Histórico de cada coleta: quando, quantos registros, erros |

Dado que não veio fica vazio. O sistema não estima nem preenche nada.

## O que foi testado e o que não foi

Testado: 35 testes automatizados cobrem a leitura do arquivo da B3, dos arquivos da CVM e das respostas do Yahoo, da CoinGecko e do Banco Central, mais uma coleta completa contra um servidor local que imita as cinco fontes.

Não testado: a coleta contra os sites reais e os arquivos `.bat` em Windows. Os formatos seguem a documentação oficial, mas só a primeira coleta real confirma.

Pontos com mais chance de precisar de ajuste:

- nomes das colunas do cadastro da CVM (arquivo FCA);
- bloqueio ou mudança no Yahoo;
- limite de chamadas da CoinGecko sem chave.

## Se algo falhar

Uma fonte com problema não derruba as outras: o erro fica registrado e aparece no resumo. Rode `diagnostico.bat` para ver qual fonte falhou e por quê.

- **B3 com "erro de certificado (SSL)":** no `config.yaml`, em `b3`, troque `verificar_certificado` para `false`.
- **Cripto com moedas sem histórico:** crie uma chave de demonstração gratuita na CoinGecko e coloque em `COINGECKO_API_KEY` no arquivo `.env`.
- **CVM com "coluna não encontrada":** a mensagem lista as colunas que vieram no arquivo.

## Estrutura

```
radar/                 código da coleta
  __main__.py          linha de comando
  db.py                esquema do banco
  rede.py              download, cache e novas tentativas
  fontes/              um módulo por fonte
tests/                 testes automatizados
docs/index.html        prévia do painel (dados fictícios)
config.yaml            configuração
.env.example           modelo para chaves de API
```

## Prévia do painel

Abra `docs/index.html` no navegador. Todos os ativos, preços e notas da prévia são inventados e servem só para mostrar o desenho.

Para ver a prévia pelo GitHub, ative o GitHub Pages em Settings, Pages, escolhendo a branch `main` e a pasta `/docs`.

## Aviso

Conteúdo informativo. Não é recomendação de compra ou venda. Oferecer recomendação de investimento ao público é atividade regulada pela CVM.

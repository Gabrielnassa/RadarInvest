"""Banco SQLite: esquema e funcoes de apoio."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ativos (
    ticker        TEXT PRIMARY KEY,
    tipo          TEXT NOT NULL,          -- acao, unit, fii, bdr, etf, outro
    nome_pregao   TEXT,
    especificacao TEXT,
    isin          TEXT,
    ultima_data   TEXT,                   -- ultimo pregao com negocio
    cnpj          TEXT,                   -- so digitos; preenchido pelo cadastro da CVM
    vinculo       TEXT                    -- 'cadastro' (codigo exato) ou 'raiz' (mesmas 4 letras)
);

CREATE TABLE IF NOT EXISTS cotacoes (
    ticker     TEXT NOT NULL,
    data       TEXT NOT NULL,             -- AAAA-MM-DD
    abertura   REAL, maxima REAL, minima REAL, medio REAL, fechamento REAL,
    negocios   INTEGER, quantidade INTEGER,
    volume     REAL,                      -- em reais
    PRIMARY KEY (ticker, data)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_cotacoes_data ON cotacoes(data);

CREATE TABLE IF NOT EXISTS empresas (
    cnpj       TEXT PRIMARY KEY,          -- so digitos
    cd_cvm     TEXT,
    nome       TEXT,
    setor      TEXT,
    situacao   TEXT,
    referencia TEXT                       -- data + versao do formulario cadastral usado
);

CREATE TABLE IF NOT EXISTS empresa_tickers (
    ticker           TEXT PRIMARY KEY,
    cnpj             TEXT NOT NULL,
    valor_mobiliario TEXT,
    mercado          TEXT,
    segmento         TEXT,
    referencia       TEXT
);
CREATE INDEX IF NOT EXISTS ix_empresa_tickers_cnpj ON empresa_tickers(cnpj);

CREATE TABLE IF NOT EXISTS demonstrativos (
    cnpj          TEXT NOT NULL,
    cd_cvm        TEXT,
    origem        TEXT NOT NULL,          -- DFP (anual) ou ITR (trimestral)
    demonstrativo TEXT NOT NULL,          -- BPA, BPP, DRE, DFC_MI
    consolidado   INTEGER NOT NULL,       -- 1 consolidado, 0 individual
    dt_refer      TEXT NOT NULL,
    dt_ini_exerc  TEXT NOT NULL DEFAULT '',
    dt_fim_exerc  TEXT,
    versao        INTEGER NOT NULL DEFAULT 1,
    cd_conta      TEXT NOT NULL,
    ds_conta      TEXT,
    valor         REAL,                   -- em reais (escala ja aplicada)
    PRIMARY KEY (cnpj, origem, demonstrativo, dt_refer, dt_ini_exerc, cd_conta)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS capital (
    cnpj     TEXT NOT NULL,
    dt_refer TEXT NOT NULL,
    versao   INTEGER NOT NULL DEFAULT 1,
    on_total REAL, pn_total REAL, total REAL,       -- acoes emitidas
    on_tes   REAL, pn_tes   REAL, tes   REAL,       -- acoes em tesouraria
    PRIMARY KEY (cnpj, dt_refer)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dividendos (
    ticker TEXT NOT NULL,
    data   TEXT NOT NULL,
    valor  REAL NOT NULL,                 -- reais por acao
    fonte  TEXT NOT NULL,
    PRIMARY KEY (ticker, data)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS desdobramentos (
    ticker TEXT NOT NULL,
    data   TEXT NOT NULL,
    fator  REAL NOT NULL,                 -- acoes depois / acoes antes (1,1 = bonificacao de 10%; 0,1 = grupamento de 10 para 1)
    fonte  TEXT NOT NULL,
    PRIMARY KEY (ticker, data)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dividendos_controle (
    ticker        TEXT PRIMARY KEY,
    atualizado_em TEXT NOT NULL,
    situacao      TEXT
);

CREATE TABLE IF NOT EXISTS cripto_ativos (
    id                TEXT PRIMARY KEY,
    simbolo           TEXT,
    nome              TEXT,
    posicao           INTEGER,            -- posicao por valor de mercado
    preco             REAL,
    valor_mercado     REAL,
    volume_24h        REAL,
    maxima_historica  REAL,
    dist_maxima_pct   REAL,
    data_maxima       TEXT,
    oferta_circulante REAL,
    moeda             TEXT,
    no_universo       INTEGER NOT NULL DEFAULT 1,
    atualizado_em     TEXT
);

CREATE TABLE IF NOT EXISTS cripto_cotacoes (
    id            TEXT NOT NULL,
    data          TEXT NOT NULL,
    preco         REAL,
    valor_mercado REAL,
    volume        REAL,
    PRIMARY KEY (id, data)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS macro (
    serie TEXT NOT NULL,
    data  TEXT NOT NULL,
    valor REAL,
    PRIMARY KEY (serie, data)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS coletas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    fonte     TEXT NOT NULL,
    inicio    TEXT NOT NULL,
    fim       TEXT,
    situacao  TEXT,                       -- ok, avisos, erro
    registros INTEGER,
    mensagem  TEXT
);

CREATE TABLE IF NOT EXISTS fii_mensal (
    cnpj     TEXT NOT NULL,
    data_ref TEXT NOT NULL,
    versao   INTEGER NOT NULL DEFAULT 1,
    nome     TEXT,
    isin     TEXT,
    segmento TEXT,
    mandato  TEXT,
    cotistas INTEGER,
    pl       REAL,                        -- patrimonio liquido em reais
    cotas    REAL,
    vp_cota  REAL,                        -- valor patrimonial por cota
    dy_mes   REAL,                        -- percentual informado pelo administrador
    PRIMARY KEY (cnpj, data_ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_fii_isin ON fii_mensal(isin);

CREATE TABLE IF NOT EXISTS tesouro (
    titulo      TEXT NOT NULL,
    vencimento  TEXT NOT NULL,
    data        TEXT NOT NULL,
    taxa_compra REAL, taxa_venda REAL,    -- % ao ano (acima do IPCA nos titulos IPCA+)
    pu_compra   REAL, pu_venda REAL,
    PRIMARY KEY (titulo, vencimento, data)
) WITHOUT ROWID;

CREATE VIEW IF NOT EXISTS macro_atual AS
SELECT m.serie, m.data, m.valor
FROM macro m
WHERE m.data = (SELECT max(data) FROM macro x WHERE x.serie = m.serie AND x.data <= date('now', 'localtime'));
"""


def conectar(caminho: Path | str) -> sqlite3.Connection:
    if str(caminho) != ":memory:":
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(caminho))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(ESQUEMA)
    conn.commit()
    return conn


def so_digitos(texto) -> str:
    return re.sub(r"\D", "", texto or "")


def vincular_ativos(conn: sqlite3.Connection) -> tuple[int, int]:
    """Liga cada acao/unit ao CNPJ da empresa usando o cadastro da CVM.
    Primeiro pelo codigo exato; depois, se as 4 letras iniciais apontarem para um unico CNPJ."""
    conn.execute(
        """UPDATE ativos SET cnpj = (SELECT cnpj FROM empresa_tickers e WHERE e.ticker = ativos.ticker),
                              vinculo = 'cadastro'
           WHERE EXISTS (SELECT 1 FROM empresa_tickers e WHERE e.ticker = ativos.ticker)"""
    )
    exatos = conn.execute("SELECT count(*) FROM ativos WHERE vinculo = 'cadastro'").fetchone()[0]
    raizes: dict[str, set[str]] = {}
    for ticker, cnpj in conn.execute("SELECT ticker, cnpj FROM empresa_tickers"):
        raizes.setdefault(ticker[:4], set()).add(cnpj)
    pendentes = conn.execute(
        "SELECT ticker FROM ativos WHERE tipo IN ('acao', 'unit') AND (vinculo IS NULL OR vinculo = 'raiz')"
    ).fetchall()
    por_raiz = 0
    for (ticker,) in pendentes:
        cnpjs = raizes.get(ticker[:4])
        if cnpjs and len(cnpjs) == 1:
            conn.execute("UPDATE ativos SET cnpj = ?, vinculo = 'raiz' WHERE ticker = ?", (next(iter(cnpjs)), ticker))
            por_raiz += 1
    conn.commit()
    return exatos, por_raiz

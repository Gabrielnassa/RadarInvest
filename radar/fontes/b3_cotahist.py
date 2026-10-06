"""Cotacoes diarias oficiais da B3, lidas do arquivo de series historicas (COTAHIST).

Layout de largura fixa (245 colunas) publicado pela B3 em SeriesHistoricas_Layout.pdf.
Os precos vem com duas casas decimais implicitas e por lote de FATCOT unidades.
"""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

MERCADO_A_VISTA = "010"
LOTE = 20000

SQL_COTACAO = """INSERT OR REPLACE INTO cotacoes
    (ticker, data, abertura, maxima, minima, medio, fechamento, negocios, quantidade, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

SQL_ATIVO = """INSERT INTO ativos (ticker, tipo, nome_pregao, especificacao, isin, ultima_data)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(ticker) DO UPDATE SET
        tipo = excluded.tipo, nome_pregao = excluded.nome_pregao,
        especificacao = excluded.especificacao, isin = excluded.isin, ultima_data = excluded.ultima_data
    WHERE ativos.ultima_data IS NULL OR excluded.ultima_data >= ativos.ultima_data"""


def _preco(txt: str, fator: int) -> float:
    txt = txt.strip()
    return (int(txt) / 100.0 / fator) if txt else 0.0


def _inteiro(txt: str) -> int:
    txt = txt.strip()
    return int(txt) if txt else 0


def parse_linha(linha: str) -> dict | None:
    """Converte um registro tipo 01 em dicionario. Devolve None para cabecalho, rodape e linhas curtas."""
    if len(linha) < 242 or linha[0:2] != "01":
        return None
    fator = _inteiro(linha[210:217]) or 1
    return {
        "data": f"{linha[2:6]}-{linha[6:8]}-{linha[8:10]}",
        "codbdi": linha[10:12],
        "ticker": linha[12:24].strip(),
        "tpmerc": linha[24:27],
        "nome_pregao": linha[27:39].strip(),
        "especificacao": linha[39:49].strip(),
        "abertura": _preco(linha[56:69], fator),
        "maxima": _preco(linha[69:82], fator),
        "minima": _preco(linha[82:95], fator),
        "medio": _preco(linha[95:108], fator),
        "fechamento": _preco(linha[108:121], fator),
        "negocios": _inteiro(linha[147:152]),
        "quantidade": _inteiro(linha[152:170]),
        "volume": _inteiro(linha[170:188]) / 100.0,
        "fator": fator,
        "isin": linha[230:242].strip(),
    }


def classificar(especificacao: str, codbdi: str) -> str:
    e = (especificacao or "").upper()
    if codbdi == "12":
        return "fii"
    if e.startswith(("ON", "PN")):
        return "acao"
    if e.startswith("UNT"):
        return "unit"
    if e.startswith("DR"):
        return "bdr"
    if e.startswith("CI"):
        return "etf"
    return "outro"


def ler_zip(caminho: Path, codbdi_aceitos) -> "iter[dict]":
    """Le o .TXT de dentro do ZIP e devolve so o mercado a vista dos tipos de lote aceitos."""
    aceitos = {str(c).zfill(2) for c in codbdi_aceitos}
    try:
        zf = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as e:
        Path(caminho).unlink(missing_ok=True)
        raise RuntimeError(f"arquivo corrompido, apagado para novo download: {Path(caminho).name}") from e
    with zf:
        nomes = [n for n in zf.namelist() if not n.endswith("/")]
        if not nomes:
            raise RuntimeError(f"ZIP vazio: {Path(caminho).name}")
        with zf.open(nomes[0]) as f:
            for bruto in f:
                if bruto[0:2] != b"01" or bruto[24:27] != b"010":
                    continue
                if bruto[10:12].decode("latin-1") not in aceitos:
                    continue
                reg = parse_linha(bruto.decode("latin-1"))
                if reg and reg["ticker"]:
                    yield reg


def planejar_arquivos(anos_presentes: set[int], ultima: date | None, hoje: date, anos: int) -> list[tuple]:
    """Decide o que baixar. Devolve tuplas ('A', ano, None) para arquivo anual ou ('M', ano, mes) para mensal.

    - anos passados que faltam no banco: arquivo anual;
    - ano passado que estava incompleto na ultima coleta: arquivo anual de novo;
    - ano corrente sem dados: arquivo anual; com dados: so os meses desde a ultima data.
    """
    plano: list[tuple] = []
    primeiro = hoje.year - max(1, anos) + 1
    for ano in range(primeiro, hoje.year):
        incompleto = ultima is not None and ultima.year == ano
        if ano not in anos_presentes or incompleto:
            plano.append(("A", ano, None))
    if hoje.year not in anos_presentes:
        plano.append(("A", hoje.year, None))
    else:
        mes_inicial = ultima.month if (ultima and ultima.year == hoje.year) else 1
        for mes in range(mes_inicial, hoje.month + 1):
            plano.append(("M", hoje.year, mes))
    return plano


def nome_arquivo(tipo: str, ano: int, mes: int | None) -> str:
    return f"COTAHIST_A{ano}.ZIP" if tipo == "A" else f"COTAHIST_M{mes:02d}{ano}.ZIP"


def gravar(conn, registros) -> int:
    total = 0
    lote: list[tuple] = []
    ativos: dict[str, tuple] = {}
    for r in registros:
        lote.append((r["ticker"], r["data"], r["abertura"], r["maxima"], r["minima"], r["medio"],
                     r["fechamento"], r["negocios"], r["quantidade"], r["volume"]))
        anterior = ativos.get(r["ticker"])
        if anterior is None or r["data"] >= anterior[5]:
            ativos[r["ticker"]] = (r["ticker"], classificar(r["especificacao"], r["codbdi"]),
                                   r["nome_pregao"], r["especificacao"], r["isin"], r["data"])
        if len(lote) >= LOTE:
            conn.executemany(SQL_COTACAO, lote)
            total += len(lote)
            lote.clear()
    if lote:
        conn.executemany(SQL_COTACAO, lote)
        total += len(lote)
    conn.executemany(SQL_ATIVO, list(ativos.values()))
    conn.commit()
    return total


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    presentes = {int(a) for (a,) in conn.execute("SELECT DISTINCT substr(data, 1, 4) FROM cotacoes")}
    ultima_txt = conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0]
    ultima = date.fromisoformat(ultima_txt) if ultima_txt else None
    plano = planejar_arquivos(presentes, ultima, hoje, int(cfg.get("anos_historico", 6)))

    total, avisos = 0, []
    for tipo, ano, mes in plano:
        nome = nome_arquivo(tipo, ano, mes)
        # arquivo de ano encerrado nao muda mais; os do ano corrente mudam todo dia
        idade = None if (tipo == "A" and ano < hoje.year and not (ultima and ultima.year == ano)) else 6
        caminho = rede.baixar(f"{base}/{nome}", cache / "b3" / nome, max_idade_horas=idade,
                              verificar=bool(cfg.get("verificar_certificado", True)))
        if caminho is None:
            avisos.append(f"{nome} ainda nao publicado pela B3")
            continue
        total += gravar(conn, ler_zip(caminho, cfg.get("codbdi", ["02", "12"])))
    return total, avisos

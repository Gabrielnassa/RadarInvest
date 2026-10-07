"""Taxas e precos do Tesouro Direto (Tesouro Transparente, dados abertos do Tesouro Nacional).

O arquivo traz o historico completo, um registro por titulo, vencimento e dia, com a taxa e o
preco de compra (o que o investidor paga) e de venda (o que recebe ao vender antes do prazo).
Guarda so os ultimos 400 dias, o bastante para a taxa de hoje e a de um ano atras.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from pathlib import Path

from .cvm import ErroFormato, numero

SQL = """INSERT OR REPLACE INTO tesouro (titulo, vencimento, data, taxa_compra, taxa_venda, pu_compra, pu_venda)
    VALUES (?, ?, ?, ?, ?, ?, ?)"""


def _iso(txt: str) -> str:
    """dd/mm/aaaa -> aaaa-mm-dd"""
    d, m, a = txt.strip().split("/")
    return f"{a}-{m.zfill(2)}-{d.zfill(2)}"


def ler(texto, desde: str) -> list[tuple]:
    leitor = csv.reader(texto, delimiter=";")
    cab = next(leitor, None)
    if not cab:
        raise ErroFormato("arquivo do Tesouro vazio")
    idx = {c.strip().lstrip("﻿").lower(): i for i, c in enumerate(cab)}

    def col(*nomes):
        for n in nomes:
            if n.lower() in idx:
                return idx[n.lower()]
        raise ErroFormato(f"coluna {nomes[0]} nao encontrada no arquivo do Tesouro. Colunas: {cab}")
    c_tit, c_venc, c_data = col("Tipo Titulo"), col("Data Vencimento"), col("Data Base")
    c_tc, c_tv = col("Taxa Compra Manha"), col("Taxa Venda Manha")
    c_pc, c_pv = col("PU Compra Manha"), col("PU Venda Manha")
    saida = []
    for linha in leitor:
        if len(linha) <= max(c_tit, c_venc, c_data, c_tc, c_tv, c_pc, c_pv):
            continue
        try:
            dia = _iso(linha[c_data])
            venc = _iso(linha[c_venc])
        except ValueError:
            continue
        if dia < desde:
            continue
        saida.append((linha[c_tit].strip(), venc, dia, numero(linha[c_tc]), numero(linha[c_tv]),
                      numero(linha[c_pc]), numero(linha[c_pv])))
    return saida


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    caminho = rede.baixar(cfg["url"], cache / "tesouro" / "PrecoTaxaTesouroDireto.csv",
                          max_idade_horas=float(cfg.get("cache_horas", 12)))
    if caminho is None:
        return 0, ["arquivo do Tesouro Direto nao encontrado no endereco configurado"]
    desde = (hoje - timedelta(days=400)).isoformat()
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    linhas = ler(io.StringIO(texto), desde)
    conn.execute("DELETE FROM tesouro WHERE data < ?", (desde,))
    conn.executemany(SQL, linhas)
    conn.commit()
    return len(linhas), [] if linhas else ["arquivo do Tesouro sem registros no ultimo ano"]

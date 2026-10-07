"""Informe mensal dos fundos imobiliarios (dados abertos da CVM).

Traz, por fundo e mes, o patrimonio liquido, o numero de cotas, o valor patrimonial da cota,
o segmento e o numero de cotistas. O codigo de negociacao nao vem no arquivo: a ligacao com a B3
e feita pelo ISIN, que tambem vem no arquivo de cotacoes (COTAHIST).
"""
from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

from ..db import so_digitos
from .cvm import ErroFormato, _abrir, _campo, _coluna, _membro, numero

SQL = """INSERT INTO fii_mensal (cnpj, data_ref, versao, nome, isin, segmento, mandato, cotistas, pl, cotas, vp_cota, dy_mes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(cnpj, data_ref) DO UPDATE SET versao = excluded.versao, nome = excluded.nome, isin = excluded.isin,
        segmento = excluded.segmento, mandato = excluded.mandato, cotistas = excluded.cotistas, pl = excluded.pl,
        cotas = excluded.cotas, vp_cota = excluded.vp_cota, dy_mes = excluded.dy_mes
    WHERE excluded.versao >= fii_mensal.versao"""


def _versao(txt: str) -> int:
    return int(txt) if txt.isdigit() else 1


def carregar(conn, caminho: Path) -> int:
    with zipfile.ZipFile(caminho) as zf:
        m_geral = _membro(zf, "_geral_")
        m_comp = _membro(zf, "_complemento_")
        if not m_geral or not m_comp:
            raise ErroFormato("informe de FII sem os arquivos geral e complemento: " + ", ".join(zf.namelist()))

        idx, linhas = _abrir(zf, m_geral)
        c_cnpj = _coluna(idx, m_geral, "CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO")
        c_ref = _coluna(idx, m_geral, "DATA_REFERENCIA")
        c_nome = _coluna(idx, m_geral, "NOME_FUNDO_CLASSE", "NOME_FUNDO", obrigatoria=False)
        c_isin = _coluna(idx, m_geral, "CODIGO_ISIN", "CODIGO_ISIN_COTA")
        c_seg = _coluna(idx, m_geral, "SEGMENTO_ATUACAO", obrigatoria=False)
        c_man = _coluna(idx, m_geral, "MANDATO", obrigatoria=False)
        geral = {}
        for linha in linhas:
            cnpj = so_digitos(_campo(linha, c_cnpj))
            if cnpj:
                geral[(cnpj, _campo(linha, c_ref))] = (_campo(linha, c_nome), _campo(linha, c_isin).upper(),
                                                       _campo(linha, c_seg), _campo(linha, c_man))

        idx, linhas = _abrir(zf, m_comp)
        c_cnpj = _coluna(idx, m_comp, "CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO")
        c_ref = _coluna(idx, m_comp, "DATA_REFERENCIA")
        c_ver = _coluna(idx, m_comp, "VERSAO", obrigatoria=False)
        c_cot = _coluna(idx, m_comp, "TOTAL_NUMERO_COTISTAS", obrigatoria=False)
        c_pl = _coluna(idx, m_comp, "PATRIMONIO_LIQUIDO")
        c_qtd = _coluna(idx, m_comp, "COTAS_EMITIDAS", obrigatoria=False)
        c_vp = _coluna(idx, m_comp, "VALOR_PATRIMONIAL_COTAS", obrigatoria=False)
        c_dy = _coluna(idx, m_comp, "PERCENTUAL_DIVIDEND_YIELD_MES", obrigatoria=False)
        lote = []
        for linha in linhas:
            cnpj = so_digitos(_campo(linha, c_cnpj))
            ref = _campo(linha, c_ref)
            if not cnpj or not ref:
                continue
            nome, isin, seg, man = geral.get((cnpj, ref), ("", "", "", ""))
            pl, cotas = numero(_campo(linha, c_pl)), numero(_campo(linha, c_qtd))
            vp = numero(_campo(linha, c_vp))
            if vp is None and pl and cotas:
                vp = pl / cotas
            cotistas = numero(_campo(linha, c_cot))
            lote.append((cnpj, ref, _versao(_campo(linha, c_ver)), nome, isin, seg, man,
                         int(cotistas) if cotistas is not None else None, pl, cotas, vp, numero(_campo(linha, c_dy))))
    conn.executemany(SQL, lote)
    conn.commit()
    return len(lote)


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    total, avisos = 0, []
    for ano in (hoje.year - 1, hoje.year):
        nome = f"inf_mensal_fii_{ano}.zip"
        caminho = rede.baixar(f"{base}/{nome}", cache / "fii" / nome,
                              max_idade_horas=float(cfg.get("cache_horas", 24)) if ano == hoje.year else 24 * 15)
        if caminho is None:
            if ano < hoje.year:
                avisos.append(f"{nome} nao encontrado")
            continue
        try:
            total += carregar(conn, caminho)
        except zipfile.BadZipFile:
            caminho.unlink(missing_ok=True)
            avisos.append(f"{nome} veio corrompido; sera baixado de novo na proxima coleta")
        except ErroFormato as e:
            avisos.append(str(e)[:300])
    return total, avisos

"""Fatos relevantes, comunicados ao mercado e avisos aos acionistas (dados abertos da CVM, IPE).

Cada linha e um documento entregue pela companhia, com categoria, assunto, data de entrega e o link
para o documento no site da CVM. Guarda so os ultimos 180 dias.
"""
from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path

from ..db import so_digitos
from .cvm import ErroFormato, _abrir, _campo, _coluna, _membro

CATEGORIAS = ("fato relevante", "comunicado ao mercado", "aviso aos acionistas")


def carregar(conn, caminho: Path, desde: str) -> int:
    with zipfile.ZipFile(caminho) as zf:
        membro = _membro(zf, "ipe_cia_aberta")
        if not membro:
            raise ErroFormato("arquivo do IPE sem o CSV esperado: " + ", ".join(zf.namelist()))
        idx, linhas = _abrir(zf, membro)
        c_cnpj = _coluna(idx, membro, "CNPJ_COMPANHIA")
        c_cat = _coluna(idx, membro, "CATEGORIA")
        c_tipo = _coluna(idx, membro, "TIPO", obrigatoria=False)
        c_esp = _coluna(idx, membro, "ESPECIE", obrigatoria=False)
        c_ass = _coluna(idx, membro, "ASSUNTO", obrigatoria=False)
        c_ent = _coluna(idx, membro, "DATA_ENTREGA")
        c_ref = _coluna(idx, membro, "DATA_REFERENCIA", obrigatoria=False)
        c_prot = _coluna(idx, membro, "PROTOCOLO_ENTREGA")
        c_link = _coluna(idx, membro, "LINK_DOWNLOAD", obrigatoria=False)
        lote = []
        for linha in linhas:
            categoria = _campo(linha, c_cat)
            entrega = _campo(linha, c_ent)[:10]
            if categoria.lower() not in CATEGORIAS or entrega < desde:
                continue
            lote.append((_campo(linha, c_prot), so_digitos(_campo(linha, c_cnpj)), entrega, _campo(linha, c_ref)[:10],
                         categoria, _campo(linha, c_tipo), _campo(linha, c_esp), _campo(linha, c_ass)[:500], _campo(linha, c_link)))
    conn.executemany("""INSERT OR REPLACE INTO ipe (protocolo, cnpj, data_entrega, data_ref, categoria, tipo, especie, assunto, link)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", lote)
    conn.commit()
    return len(lote)


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    desde = (hoje - timedelta(days=180)).isoformat()
    total, avisos = 0, []
    for ano in sorted({int(desde[:4]), hoje.year}):
        nome = f"ipe_cia_aberta_{ano}.zip"
        caminho = rede.baixar(f"{base}/IPE/DADOS/{nome}", cache / "ipe" / nome,
                              max_idade_horas=float(cfg.get("cache_horas", 12)) if ano == hoje.year else 24 * 7)
        if caminho is None:
            avisos.append(f"{nome} nao encontrado")
            continue
        try:
            total += carregar(conn, caminho, desde)
        except zipfile.BadZipFile:
            caminho.unlink(missing_ok=True)
            avisos.append(f"{nome} veio corrompido; sera baixado de novo na proxima coleta")
        except ErroFormato as e:
            avisos.append(str(e)[:300])
    conn.execute("DELETE FROM ipe WHERE data_entrega < ?", (desde,))
    conn.commit()
    return total, avisos

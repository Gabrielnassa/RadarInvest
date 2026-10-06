"""Dados abertos da CVM: cadastro das companhias (FCA) e demonstrativos (DFP anual, ITR trimestral).

Os arquivos sao ZIPs com CSVs separados por ';' em latin-1. As colunas sao localizadas pelo
nome, e nao pela posicao, para que uma coluna nova no arquivo nao quebre a leitura.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path

from ..db import so_digitos

RE_TICKER = re.compile(r"^[A-Z]{4}\d{1,2}$")
LOTE = 20000

SQL_DEMONSTRATIVO = """INSERT INTO demonstrativos
    (cnpj, cd_cvm, origem, demonstrativo, consolidado, dt_refer, dt_ini_exerc, dt_fim_exerc,
     versao, cd_conta, ds_conta, valor)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(cnpj, origem, demonstrativo, dt_refer, dt_ini_exerc, cd_conta) DO UPDATE SET
        cd_cvm = excluded.cd_cvm, consolidado = excluded.consolidado, dt_fim_exerc = excluded.dt_fim_exerc,
        versao = excluded.versao, ds_conta = excluded.ds_conta, valor = excluded.valor
    WHERE excluded.versao >= demonstrativos.versao"""


class ErroFormato(Exception):
    """O arquivo da CVM nao tem a estrutura esperada."""


def numero(txt) -> float | None:
    t = (txt or "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _membro(zf: zipfile.ZipFile, trecho: str) -> str | None:
    alvo = trecho.lower()
    for nome in zf.namelist():
        if alvo in nome.lower():
            return nome
    return None


def _abrir(zf: zipfile.ZipFile, membro: str):
    """Devolve (indice_das_colunas, leitor_de_linhas)."""
    texto = io.TextIOWrapper(zf.open(membro), encoding="latin-1", newline="")
    leitor = csv.reader(texto, delimiter=";", quoting=csv.QUOTE_NONE)
    cabecalho = next(leitor, None)
    if not cabecalho:
        raise ErroFormato(f"{membro} esta vazio")
    indice = {c.strip().lstrip("﻿").upper(): i for i, c in enumerate(cabecalho)}
    return indice, leitor


def _coluna(indice: dict, membro: str, *nomes: str, obrigatoria: bool = True) -> int | None:
    for nome in nomes:
        if nome.upper() in indice:
            return indice[nome.upper()]
    if obrigatoria:
        raise ErroFormato(f"coluna {nomes[0]} nao encontrada em {membro}. Colunas do arquivo: {sorted(indice)}")
    return None


def _campo(linha: list, i: int | None) -> str:
    return linha[i].strip() if i is not None and i < len(linha) else ""


# ---------------------------------------------------------------- cadastro (FCA)

def carregar_fca(conn, caminho: Path, hoje: date) -> tuple[int, int]:
    """Grava empresas (CNPJ, codigo CVM, setor) e os codigos de negociacao de cada uma."""
    with zipfile.ZipFile(caminho) as zf:
        n_emp = _fca_geral(conn, zf)
        n_tic = _fca_valores(conn, zf, hoje)
    conn.commit()
    return n_emp, n_tic


def _fca_geral(conn, zf) -> int:
    membro = _membro(zf, "fca_cia_aberta_geral_")
    if not membro:
        return 0
    idx, linhas = _abrir(zf, membro)
    c_cnpj = _coluna(idx, membro, "CNPJ_COMPANHIA", "CNPJ_CIA")
    c_ref = _coluna(idx, membro, "DATA_REFERENCIA", "DT_REFER")
    c_ver = _coluna(idx, membro, "VERSAO", obrigatoria=False)
    c_nome = _coluna(idx, membro, "NOME_EMPRESARIAL", "DENOM_CIA", obrigatoria=False)
    c_cvm = _coluna(idx, membro, "CODIGO_CVM", "CD_CVM", obrigatoria=False)
    c_setor = _coluna(idx, membro, "SETOR_ATIVIDADE", obrigatoria=False)
    c_sit = _coluna(idx, membro, "SITUACAO_REGISTRO_CVM", "SITUACAO_EMISSOR", obrigatoria=False)
    melhores: dict[str, tuple] = {}
    for linha in linhas:
        cnpj = so_digitos(_campo(linha, c_cnpj))
        if not cnpj:
            continue
        ref = _referencia(_campo(linha, c_ref), _campo(linha, c_ver))
        if cnpj not in melhores or ref >= melhores[cnpj][5]:
            melhores[cnpj] = (cnpj, _campo(linha, c_cvm), _campo(linha, c_nome),
                              _campo(linha, c_setor), _campo(linha, c_sit), ref)
    conn.executemany(
        """INSERT INTO empresas (cnpj, cd_cvm, nome, setor, situacao, referencia) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(cnpj) DO UPDATE SET cd_cvm = excluded.cd_cvm, nome = excluded.nome,
               setor = excluded.setor, situacao = excluded.situacao, referencia = excluded.referencia
           WHERE empresas.referencia IS NULL OR excluded.referencia >= empresas.referencia""",
        list(melhores.values()),
    )
    return len(melhores)


def _fca_valores(conn, zf, hoje: date) -> int:
    membro = _membro(zf, "fca_cia_aberta_valor_mobiliario_")
    if not membro:
        raise ErroFormato("arquivo de valores mobiliarios nao encontrado no ZIP do FCA: " + ", ".join(zf.namelist()))
    idx, linhas = _abrir(zf, membro)
    c_cnpj = _coluna(idx, membro, "CNPJ_COMPANHIA", "CNPJ_CIA")
    c_ref = _coluna(idx, membro, "DATA_REFERENCIA", "DT_REFER")
    c_ver = _coluna(idx, membro, "VERSAO", obrigatoria=False)
    c_tic = _coluna(idx, membro, "CODIGO_NEGOCIACAO")
    c_vm = _coluna(idx, membro, "VALOR_MOBILIARIO", obrigatoria=False)
    c_mer = _coluna(idx, membro, "MERCADO", obrigatoria=False)
    c_seg = _coluna(idx, membro, "SEGMENTO", obrigatoria=False)
    c_fim = _coluna(idx, membro, "DATA_FIM_NEGOCIACAO", obrigatoria=False)
    hoje_txt = hoje.isoformat()

    # o formulario mais recente de cada empresa define os codigos vigentes
    por_empresa: dict[str, tuple[str, list]] = {}
    for linha in linhas:
        cnpj = so_digitos(_campo(linha, c_cnpj))
        if not cnpj:
            continue
        ref = _referencia(_campo(linha, c_ref), _campo(linha, c_ver))
        atual = por_empresa.get(cnpj)
        if atual is None or ref > atual[0]:
            atual = por_empresa[cnpj] = (ref, [])
        elif ref < atual[0]:
            continue
        ticker = _campo(linha, c_tic).upper()
        fim = _campo(linha, c_fim)
        if not RE_TICKER.match(ticker) or (fim and fim <= hoje_txt):
            continue
        atual[1].append((ticker, cnpj, _campo(linha, c_vm), _campo(linha, c_mer), _campo(linha, c_seg), ref))

    gravados = 0
    for cnpj, (ref, itens) in por_empresa.items():
        conn.execute("DELETE FROM empresa_tickers WHERE cnpj = ? AND referencia < ?", (cnpj, ref))
        for item in itens:
            cur = conn.execute(
                """INSERT INTO empresa_tickers (ticker, cnpj, valor_mobiliario, mercado, segmento, referencia)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET cnpj = excluded.cnpj,
                       valor_mobiliario = excluded.valor_mobiliario, mercado = excluded.mercado,
                       segmento = excluded.segmento, referencia = excluded.referencia
                   WHERE excluded.referencia >= empresa_tickers.referencia""",
                item,
            )
            gravados += cur.rowcount if cur.rowcount > 0 else 0
    return gravados


def _referencia(data_ref: str, versao: str) -> str:
    v = versao if versao.isdigit() else "0"
    return f"{data_ref}#{int(v):04d}"


# ---------------------------------------------------------------- demonstrativos (DFP e ITR)

def carregar_demonstrativos(conn, caminho: Path, origem: str, demonstrativos, nivel_max: int,
                            permitidos: set[str] | None = None) -> int:
    """Grava as contas do exercicio mais recente de cada documento.
    Usa o consolidado quando a empresa publica um; senao, o individual."""
    prefixo = origem.lower()
    com_consolidado: set[tuple[str, str]] = set()
    total = 0
    with zipfile.ZipFile(caminho) as zf:
        for escopo, consolidado in (("con", 1), ("ind", 0)):
            for dem in demonstrativos:
                membro = _membro(zf, f"{prefixo}_cia_aberta_{dem}_{escopo}_")
                if membro:
                    total += _carregar_csv(conn, zf, membro, origem.upper(), dem, consolidado,
                                           nivel_max, permitidos, com_consolidado)
    conn.commit()
    return total


def _carregar_csv(conn, zf, membro, origem, dem, consolidado, nivel_max, permitidos, com_consolidado) -> int:
    idx, linhas = _abrir(zf, membro)
    c_cnpj = _coluna(idx, membro, "CNPJ_CIA")
    c_ref = _coluna(idx, membro, "DT_REFER")
    c_ver = _coluna(idx, membro, "VERSAO", obrigatoria=False)
    c_cvm = _coluna(idx, membro, "CD_CVM", obrigatoria=False)
    c_esc = _coluna(idx, membro, "ESCALA_MOEDA", obrigatoria=False)
    c_ord = _coluna(idx, membro, "ORDEM_EXERC")
    c_ini = _coluna(idx, membro, "DT_INI_EXERC", obrigatoria=False)
    c_fim = _coluna(idx, membro, "DT_FIM_EXERC", obrigatoria=False)
    c_cd = _coluna(idx, membro, "CD_CONTA")
    c_ds = _coluna(idx, membro, "DS_CONTA", obrigatoria=False)
    c_vl = _coluna(idx, membro, "VL_CONTA")

    total, lote = 0, []
    for linha in linhas:
        if not _campo(linha, c_ord).upper().startswith(("ÚLT", "ULT")):
            continue
        cnpj = so_digitos(_campo(linha, c_cnpj))
        if not cnpj or (permitidos is not None and cnpj not in permitidos):
            continue
        dt_refer = _campo(linha, c_ref)
        chave = (cnpj, dt_refer)
        if consolidado:
            com_consolidado.add(chave)
        elif chave in com_consolidado:
            continue
        conta = _campo(linha, c_cd)
        if not conta or conta.count(".") + 1 > nivel_max:
            continue
        valor = numero(_campo(linha, c_vl))
        if valor is not None and _campo(linha, c_esc).upper().startswith("MIL"):
            valor *= 1000.0
        versao = _campo(linha, c_ver)
        lote.append((cnpj, _campo(linha, c_cvm), origem, dem, consolidado, dt_refer, _campo(linha, c_ini),
                     _campo(linha, c_fim), int(versao) if versao.isdigit() else 1, conta,
                     _campo(linha, c_ds), valor))
        if len(lote) >= LOTE:
            conn.executemany(SQL_DEMONSTRATIVO, lote)
            total += len(lote)
            lote.clear()
    if lote:
        conn.executemany(SQL_DEMONSTRATIVO, lote)
        total += len(lote)
    return total


# ---------------------------------------------------------------- coleta

def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    pasta = cache / "cvm"
    horas = float(cfg.get("cache_horas", 24))
    avisos: list[str] = []
    total = 0

    tickers = 0
    for ano in (hoje.year - 1, hoje.year):
        nome = f"fca_cia_aberta_{ano}.zip"
        caminho = rede.baixar(f"{base}/FCA/DADOS/{nome}", pasta / nome, max_idade_horas=horas)
        if caminho is None:
            avisos.append(f"{nome} nao encontrado")
            continue
        try:
            _, n = carregar_fca(conn, caminho, hoje)
        except zipfile.BadZipFile:
            caminho.unlink(missing_ok=True)
            avisos.append(f"{nome} veio corrompido; sera baixado de novo na proxima coleta")
            continue
        tickers += n
    if tickers == 0:
        avisos.append("cadastro da CVM sem codigos de negociacao: acoes ficarao sem vinculo com a empresa")

    permitidos = None
    if cfg.get("somente_listadas", False):
        permitidos = {c for (c,) in conn.execute("SELECT DISTINCT cnpj FROM empresa_tickers")}
        if not permitidos:
            permitidos = None
            avisos.append("somente_listadas ignorado porque o cadastro veio vazio")

    anos = max(1, int(cfg.get("anos_fundamentos", 6)))
    dems = cfg.get("demonstrativos", ["BPA", "BPP", "DRE", "DFC_MI"])
    nivel = int(cfg.get("nivel_max_conta", 3))
    plano = [("DFP", a) for a in range(hoje.year - anos, hoje.year + 1)]
    plano += [("ITR", a) for a in range(hoje.year - anos + 1, hoje.year + 1)]
    for origem, ano in plano:
        nome = f"{origem.lower()}_cia_aberta_{ano}.zip"
        # anos antigos quase nunca mudam; os dois mais recentes recebem reapresentacoes
        idade = horas if ano >= hoje.year - 1 else 24 * 30
        caminho = rede.baixar(f"{base}/{origem}/DADOS/{nome}", pasta / nome, max_idade_horas=idade)
        if caminho is None:
            if ano < hoje.year:
                avisos.append(f"{nome} nao encontrado")
            continue
        try:
            total += carregar_demonstrativos(conn, caminho, origem, dems, nivel, permitidos)
        except zipfile.BadZipFile:
            caminho.unlink(missing_ok=True)
            avisos.append(f"{nome} veio corrompido; sera baixado de novo na proxima coleta")
    return total, avisos

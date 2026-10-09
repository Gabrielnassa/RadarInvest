"""Gera o arquivo que o painel le (docs/dados.json) a partir do banco."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from datetime import date, timedelta

from . import backtest, db, notas, resumos


def _limpar(x):
    """Arredonda numeros e troca NaN/infinito por None, para o JSON ficar valido e pequeno."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 4) if abs(x) < 1000 else round(x, 2)
    if isinstance(x, dict):
        return {k: _limpar(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_limpar(v) for v in x]
    return x


def macro(conn) -> dict:
    saida = {}
    for serie, dia, valor in conn.execute("SELECT serie, data, valor FROM macro_atual"):
        saida[serie] = {"valor": valor, "data": dia}
    ipca = [v for (v,) in conn.execute(
        "SELECT valor FROM macro WHERE serie = 'ipca_mensal' AND data <= date('now') ORDER BY data DESC LIMIT 12")]
    if len(ipca) == 12:
        acumulado = 1.0
        for v in ipca:
            acumulado *= 1 + v / 100
        saida["ipca_12m"] = {"valor": (acumulado - 1) * 100, "data": saida.get("ipca_mensal", {}).get("data")}
    return saida


def coletas(conn) -> list[dict]:
    return [{"fonte": f, "fim": fim, "situacao": s, "registros": r, "mensagem": m or ""}
            for f, fim, s, r, m in conn.execute(
                """SELECT fonte, fim, situacao, registros, mensagem FROM coletas
                   WHERE id IN (SELECT max(id) FROM coletas GROUP BY fonte) ORDER BY fonte""")]


def sem_vinculo(conn, volume_minimo: float) -> list[str]:
    """Acoes com liquidez que ficaram fora do ranking por nao estarem ligadas a uma empresa da CVM."""
    return [t for (t,) in conn.execute(
        """WITH ultimos AS (SELECT DISTINCT data FROM cotacoes ORDER BY data DESC LIMIT 60)
           SELECT a.ticker FROM ativos a JOIN cotacoes c ON c.ticker = a.ticker
           WHERE a.tipo IN ('acao', 'unit') AND a.cnpj IS NULL AND c.data IN (SELECT data FROM ultimos)
           GROUP BY a.ticker HAVING sum(c.volume) / 60.0 >= ? ORDER BY sum(c.volume) DESC""", (volume_minimo,))]


def precos_mensais(conn, tickers: list[str], anos: int = 5) -> dict[str, list]:
    """Ultimo fechamento de cada mes nos ultimos `anos`, na base de acoes de hoje: {ticker: [[aaaa-mm, preco]]}."""
    ultima = conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0]
    if not ultima or not tickers:
        return {}
    desde = (date.fromisoformat(ultima) - timedelta(days=365 * anos + 31)).isoformat()
    splits: dict[str, list] = {}
    for t, d, f in conn.execute("SELECT ticker, data, fator FROM desdobramentos WHERE fator > 0"):
        splits.setdefault(t, []).append((d, f))
    saida: dict[str, dict] = {}
    marcas = ",".join("?" * len(tickers))
    for t, d, fech in conn.execute(f"SELECT ticker, data, fechamento FROM cotacoes WHERE ticker IN ({marcas}) AND data >= ? "
                                   "ORDER BY ticker, data", (*tickers, desde)):
        if fech:
            fator = 1.0
            for quando, f in splits.get(t, ()):
                if quando > d:
                    fator *= f
            saida.setdefault(t, {})[d[:7]] = round(fech / fator, 2)
    return {t: [[m, v] for m, v in sorted(meses.items())] for t, meses in saida.items()}


def cdi_mensal(conn, anos: int = 6) -> list:
    """Indice do CDI acumulado no fim de cada mes (base 1 no inicio): [[aaaa-mm, indice]]."""
    desde = (date.today() - timedelta(days=365 * anos)).isoformat()
    indice, saida, mes = 1.0, {}, None
    for d, v in conn.execute("SELECT data, valor FROM macro WHERE serie = 'cdi_anual' AND data >= ? ORDER BY data", (desde,)):
        if v is not None:
            indice *= (1 + v / 100) ** (1 / 252)
        saida[d[:7]] = round(indice, 6)
    return [[m, v] for m, v in sorted(saida.items())]


def tesouro(conn) -> dict:
    """Titulos a venda na data mais recente, com a taxa de um ano antes para comparar."""
    ultima = conn.execute("SELECT max(data) FROM tesouro").fetchone()[0]
    if not ultima:
        return {"data": None, "titulos": []}
    ano_antes = (date.fromisoformat(ultima) - timedelta(days=365)).isoformat()
    titulos = []
    for tit, venc, tc, tv, pc, pv in conn.execute(
            "SELECT titulo, vencimento, taxa_compra, taxa_venda, pu_compra, pu_venda FROM tesouro WHERE data = ? ORDER BY titulo, vencimento",
            (ultima,)):
        antes = conn.execute("SELECT taxa_venda FROM tesouro WHERE titulo = ? AND vencimento = ? AND data <= ? ORDER BY data DESC LIMIT 1",
                             (tit, venc, ano_antes)).fetchone()
        serie = [[d[:7], v] for d, v in conn.execute(
            """SELECT max(data), taxa_venda FROM tesouro WHERE titulo = ? AND vencimento = ? GROUP BY substr(data, 1, 7) ORDER BY 1""",
            (tit, venc))]
        titulos.append({"titulo": tit, "venc": venc, "taxaCompra": tc, "taxaVenda": tv, "puCompra": pc, "puVenda": pv,
                        "aVenda": bool(tc and tc > 0 and pc and pc > 0), "taxaAnoAntes": antes[0] if antes else None,
                        "serie": serie})
    return {"data": ultima, "titulos": titulos}


def prazo_balanco(balanco_em: str | None) -> tuple[str | None, str | None]:
    """Proximo trimestre a ser entregue e o prazo legal da CVM: 45 dias para o ITR (1o a 3o trimestres)
    e 3 meses para as demonstracoes anuais (DFP)."""
    if not balanco_em:
        return None, None
    a, m = int(balanco_em[:4]), int(balanco_em[5:7])
    m += 3
    if m > 12:
        a, m = a + 1, m - 12
    fim = date(a, m, 31 if m in (3, 12) else 30)
    prazo = date(a + 1, 3, 31) if m == 12 else fim + timedelta(days=45)
    return fim.isoformat(), prazo.isoformat()


def calendario(conn, nomes: dict, hoje: str) -> list[dict]:
    """Proventos anunciados com pagamento a partir de hoje (ou data com futura), da B3."""
    saida = []
    for t, data_com, tipo, valor, pag in conn.execute(
            """SELECT ticker, data_com, tipo, valor, pagamento FROM proventos_b3
               WHERE coalesce(pagamento, data_com) >= ? OR data_com >= ? ORDER BY coalesce(pagamento, data_com)""", (hoje, hoje)):
        if t in nomes:
            saida.append({"t": t, "n": nomes[t], "tipo": tipo.title(), "valor": valor, "dataCom": data_com, "pagamento": pag})
    return saida


def fatos(conn, cnpj_ticker: dict, desde: str) -> list[dict]:
    saida = []
    for cnpj, entrega, cat, tipo, esp, assunto, link in conn.execute(
            """SELECT cnpj, data_entrega, categoria, tipo, especie, assunto, link FROM ipe
               WHERE data_entrega >= ? ORDER BY data_entrega DESC, protocolo DESC""", (desde,)):
        if cnpj in cnpj_ticker:
            saida.append({"t": cnpj_ticker[cnpj], "data": entrega, "cat": cat, "assunto": assunto or tipo or esp, "link": link})
    return saida


def montar(conn, cfg: dict) -> dict:
    regras = cfg.get("notas") or {}
    acoes = notas.calcular_acoes(conn, regras)
    cripto = notas.calcular_cripto(conn, regras)
    fiis = notas.calcular_fiis(conn, regras)
    teste = backtest.rodar(conn, regras)
    historico = teste.pop("historico", {})
    mensais = precos_mensais(conn, [a["t"] for a in acoes] + [f["t"] for f in fiis] + [backtest.REFERENCIA])
    ultima = conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0] or date.today().isoformat()
    for f in fiis:
        f["precos5"] = mensais.get(f["t"], [])
    for a in acoes:
        a["proxPeriodo"], a["proxPrazo"] = prazo_balanco(a.get("balancoEm"))
        a["hist"] = historico.get(a["t"], []) + [[a["data"], round(a["final"]), a.get("lp")]]
        a["precos5"] = mensais.get(a["t"], [])
    return _limpar({
        "geradoEm": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataCotacao": conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0],
        "pesos": dict(notas.PADRAO["pesos"], **(regras.get("pesos") or {})),
        "acoes": acoes,
        "cripto": cripto,
        "fiis": fiis,
        "tesouro": tesouro(conn),
        "backtest": teste,
        "ibovMensal": mensais.get(backtest.REFERENCIA, []),
        "cdiMensal": cdi_mensal(conn),
        "calendario": calendario(conn, {a["t"]: a["n"] for a in acoes}, ultima),
        "fatos": fatos(conn, {c: t for t, c in conn.execute("SELECT ticker, cnpj FROM ativos WHERE cnpj IS NOT NULL")
                              if t in {a["t"] for a in acoes}}, (date.fromisoformat(ultima) - timedelta(days=90)).isoformat()),
        "macro": macro(conn),
        "coleta": coletas(conn),
        "semVinculo": sem_vinculo(conn, float(regras.get("volume_minimo", notas.PADRAO["volume_minimo"]))),
    })


class ErroValidacao(Exception):
    """Os dados novos parecem quebrados: o arquivo publicado anterior e mantido."""


def validar(novo: dict, anterior: dict | None) -> list[str]:
    """Problemas que impedem publicar. Compara com a publicacao anterior quando ela existe."""
    erros = []
    acoes = novo.get("acoes") or []
    if len(acoes) < 30:
        erros.append(f"so {len(acoes)} acoes com nota (minimo 30)")
    if anterior and len(anterior.get("acoes") or []) >= 30 and len(acoes) < 0.7 * len(anterior["acoes"]):
        erros.append(f"acoes com nota cairam de {len(anterior['acoes'])} para {len(acoes)}")
    ruins = [a["t"] for a in acoes if not a.get("p") or a["p"] <= 0 or a.get("final") is None or not 0 <= a["final"] <= 100]
    if ruins:
        erros.append(f"preco ou nota fora do esperado em {len(ruins)} acoes (ex.: {', '.join(ruins[:5])})")
    if anterior and anterior.get("fiis") and len(novo.get("fiis") or []) < 0.5 * len(anterior["fiis"]):
        erros.append(f"FIIs cairam de {len(anterior['fiis'])} para {len(novo.get('fiis') or [])}")
    if anterior and anterior.get("acoes"):
        antes = {a["t"]: a.get("final") for a in anterior["acoes"] if a.get("final") is not None}
        comuns = [(antes[a["t"]], a["final"]) for a in acoes if a["t"] in antes and a.get("final") is not None]
        if len(comuns) >= 30:
            queda = sum(x - y for x, y in comuns) / len(comuns)
            if queda > 15:
                erros.append(f"a nota media das mesmas acoes caiu {queda:.0f} pontos de uma coleta para outra")
    if anterior and anterior.get("dataCotacao") and novo.get("dataCotacao") and novo["dataCotacao"] < anterior["dataCotacao"]:
        erros.append(f"data das cotacoes voltou de {anterior['dataCotacao']} para {novo['dataCotacao']}")
    return erros


DETALHE = ("precos5", "hist", "anual")


def separar(dados: dict) -> dict:
    """Deixa o arquivo principal leve: series longas de cada ativo e comunicados da CVM vao para um arquivo
    carregado depois da primeira tela; os textos dos checklists vao uma vez so, e cada acao leva so os sim/nao."""
    detalhes = {}
    for lista in ("acoes", "fiis"):
        for item in dados.get(lista) or []:
            extra = {k: item.pop(k) for k in DETALHE if k in item}
            if extra:
                detalhes[item["t"]] = extra
    if "fatos" in dados:
        detalhes["_fatos"] = dados.pop("fatos")
    for campo in ("check", "lpCheck"):
        rotulos: list[str] = []
        for a in dados.get("acoes") or []:
            if a.get(campo):
                for txt, _ in a[campo]:
                    if txt not in rotulos:
                        rotulos.append(txt)
                a[campo] = [[rotulos.index(txt), ok] for txt, ok in a[campo]]
        dados["rotulos_" + campo] = rotulos
    return detalhes


def gravar_historico(pasta: Path, dados: dict) -> None:
    """Uma foto compacta das notas por dia de cotacao, para auditar mudancas: historico/AAAA-MM-DD.json."""
    dia = dados.get("dataCotacao")
    if not dia:
        return
    pasta.mkdir(parents=True, exist_ok=True)
    foto = {"data": dia, "acoes": [[a["t"], round(a["final"]), a.get("lp")] for a in dados.get("acoes") or []],
            "fiis": [[f["t"], f.get("nota")] for f in dados.get("fiis") or []]}
    (pasta / f"{dia}.json").write_text(json.dumps(foto, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dias = sorted(x.stem for x in pasta.glob("????-??-??.json"))
    (pasta / "indice.json").write_text(json.dumps(dias), encoding="utf-8")


def exportar(cfg: dict, destino: Path | None = None) -> tuple[Path, dict]:
    destino = Path(destino or (Path(cfg["_banco"]).parent.parent / "docs" / "dados.json"))
    conn = db.conectar(cfg["_banco"])
    try:
        dados = montar(conn, cfg)
    finally:
        conn.close()
    anterior = None
    if destino.exists():
        try:
            anterior = json.loads(destino.read_text(encoding="utf-8"))
        except ValueError:
            anterior = None
    novos = resumos.aplicar(dados, Path(cfg["_banco"]).parent)
    if novos:
        print(f"{novos} resumos novos escritos pelo Claude")
    erros = validar(dados, anterior)
    if erros:
        raise ErroValidacao("; ".join(erros))
    detalhes = separar(dados)
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    (destino.parent / "detalhes.json").write_text(texto(detalhes), encoding="utf-8")
    destino.write_text(texto(dados), encoding="utf-8")
    gravar_historico(destino.parent / "historico", dados)
    return destino, dados

"""Etapa 3: teste das notas no passado.

Em datas passadas (maio e novembro de cada ano), recalcula as notas como se fosse aquele dia,
usando so cotacoes, dividendos e balancos que ja existiam (balanco conta 90 dias depois do fim
do trimestre). Depois mede o retorno dos 12 meses seguintes, somando dividendos, de tres grupos:
as 10 maiores notas finais, as 10 maiores notas de longo prazo e todas as acoes do ranking.

Limites que o painel tambem mostra: so entram empresas que ainda estao no cadastro da CVM
(as que fecharam o capital ficam de fora, o que favorece o resultado) e o periodo e curto.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import notas

TOPO = 10
MESES_INICIO = (5, 11)          # depois da entrega do balanco anual (marco) e do 2o trimestre (agosto)
LIQUIDEZ_LONGO = 2_000_000


def datas_teste(primeira_cotacao: str, ultima_cotacao: str, meses=MESES_INICIO) -> list[str]:
    """Primeiro dia de cada mes de inicio que tenha pelo menos 400 dias de cotacao antes
    (12 meses para a variacao e para o volume) e ao menos 30 dias depois."""
    ini, fim = date.fromisoformat(primeira_cotacao), date.fromisoformat(ultima_cotacao)
    saida = []
    for ano in range(ini.year, fim.year + 1):
        for mes in meses:
            d = date(ano, mes, 1)
            if d - ini >= timedelta(days=400) and fim - d >= timedelta(days=30):
                saida.append(d.isoformat())
    return saida


def _fator(splits: list, depois_de: str) -> float:
    f = 1.0
    for quando, fator in splits:
        if quando > depois_de:
            f *= fator
    return f


def retorno_acao(conn, ticker: str, inicio: str, fim: str, splits: list, divs: list) -> float | None:
    """Retorno de `inicio` a `fim` com dividendos, tudo na base de acoes de hoje
    (o Yahoo ja entrega os dividendos ajustados por desdobramentos)."""
    a = conn.execute("SELECT data, fechamento FROM cotacoes WHERE ticker = ? AND data <= ? ORDER BY data DESC LIMIT 1",
                     (ticker, inicio)).fetchone()
    b = conn.execute("SELECT data, fechamento FROM cotacoes WHERE ticker = ? AND data <= ? AND data > ? ORDER BY data DESC LIMIT 1",
                     (ticker, fim, inicio)).fetchone()
    if not a or not b or not a[1] or not b[1]:
        return None
    p0 = a[1] / _fator(splits, a[0])
    p1 = b[1] / _fator(splits, b[0])
    recebido = sum(v for d, v in divs if a[0] < d <= b[0])
    return (p1 + recebido) / p0 - 1


def retorno_cdi(conn, inicio: str, fim: str) -> float | None:
    """CDI acumulado no periodo, pela taxa anual diaria do Banco Central (252 dias uteis)."""
    taxas = conn.execute("SELECT data, valor FROM macro WHERE serie = 'cdi_anual' AND data > ? AND data <= ? ORDER BY data",
                         (inicio, fim)).fetchall()
    primeira = conn.execute("SELECT min(data) FROM macro WHERE serie = 'cdi_anual'").fetchone()[0]
    if not taxas or not primeira or primeira > (date.fromisoformat(inicio) + timedelta(days=10)).isoformat():
        return None
    acumulado = 1.0
    for _, v in taxas:
        if v is not None:
            acumulado *= (1 + v / 100) ** (1 / 252)
    return acumulado - 1


def media(valores: list) -> float | None:
    v = [x for x in valores if x is not None]
    return sum(v) / len(v) if v else None


def encadear(retornos: list) -> float | None:
    if not retornos or any(r is None for r in retornos):
        return None
    total = 1.0
    for r in retornos:
        total *= 1 + r
    return total - 1


def rodar(conn, cfg: dict | None = None, meses=MESES_INICIO) -> dict:
    lim = conn.execute("SELECT min(data), max(data) FROM cotacoes").fetchone()
    if not lim or not lim[0]:
        return {"periodos": [], "historico": {}}
    datas = datas_teste(lim[0], lim[1], meses)
    splits: dict[str, list] = {}
    for t, d, f in conn.execute("SELECT ticker, data, fator FROM desdobramentos WHERE fator > 0 ORDER BY data"):
        splits.setdefault(t, []).append((d, f))
    divs: dict[str, list] = {}
    for t, d, v in conn.execute("SELECT ticker, data, valor FROM dividendos ORDER BY data"):
        divs.setdefault(t, []).append((d, v))

    periodos, historico = [], {}
    for inicio in datas:
        ranking = notas.calcular_acoes(conn, cfg, ate=inicio)
        if len(ranking) < TOPO * 2:
            continue
        for r in ranking:
            historico.setdefault(r["t"], []).append([inicio, round(r["final"]), r.get("lp")])
        fim_alvo = (date.fromisoformat(inicio) + timedelta(days=365)).isoformat()
        fim = min(fim_alvo, lim[1])
        ret = {r["t"]: retorno_acao(conn, r["t"], inicio, fim, splits.get(r["t"], []), divs.get(r["t"], []))
               for r in ranking}
        top = ranking[:TOPO]
        longo = sorted([r for r in ranking if r.get("lp") is not None and r["vol"] >= LIQUIDEZ_LONGO],
                       key=lambda r: (-r["lp"], -r["final"]))[:TOPO]
        periodos.append({
            "inicio": inicio, "fim": fim, "completo": fim == fim_alvo, "n": len(ranking),
            "top": media([ret[r["t"]] for r in top]),
            "longo": media([ret[r["t"]] for r in longo]) if len(longo) >= 5 else None,
            "universo": media(list(ret.values())),
            "cdi": retorno_cdi(conn, inicio, fim),
            "acoesTop": [[r["t"], round(r["final"]), ret[r["t"]]] for r in top],
            "acoesLongo": [[r["t"], r["lp"], ret[r["t"]]] for r in longo],
        })

    # carteira trocada uma vez por ano (so os periodos de maio completos, que nao se sobrepoem)
    anuais = [p for p in periodos if p["completo"] and p["inicio"][5:7] == f"{meses[0]:02d}"]
    acumulado = {k: encadear([p[k] for p in anuais]) for k in ("top", "universo", "cdi")} if anuais else None
    # a nota de longo prazo precisa de 5 anos de balanco: so existe nos periodos mais recentes
    com_longo = [p for p in anuais if p["longo"] is not None]
    acumulado_longo = None
    if com_longo:
        acumulado_longo = {"desde": com_longo[0]["inicio"], "anos": len(com_longo),
                           **{k: encadear([p[k] for p in com_longo]) for k in ("longo", "universo", "cdi")}}
    completos = [p for p in periodos if p["completo"] and p["top"] is not None and p["universo"] is not None]
    return {
        "periodos": periodos,
        "acumulado": acumulado,
        "acumuladoLongo": acumulado_longo,
        "anosAcumulados": len(anuais),
        "venceuUniverso": [sum(1 for p in completos if p["top"] > p["universo"]), len(completos)],
        "venceuLongo": [sum(1 for p in completos if p["longo"] is not None and p["longo"] > p["universo"]),
                        sum(1 for p in completos if p["longo"] is not None)],
        "historico": historico,
    }

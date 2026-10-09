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
REFERENCIA = "BOVA11"            # fundo que replica o Ibovespa, com cotacao no mesmo arquivo da B3
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


def topo_por(ranking: list, chave) -> list:
    """As TOPO maiores pela funcao `chave` (None fica de fora)."""
    com = [(chave(r), r) for r in ranking]
    com = [(v, r) for v, r in com if v is not None]
    com.sort(key=lambda x: -x[0])
    return [r for _, r in com[:TOPO]]


def sugerir_pesos(excessos: dict, padrao: dict) -> dict:
    """Mistura meio a meio os pesos padrao com pesos proporcionais ao ganho medio de cada metodo sobre
    todas as acoes (metodo que perdeu fica so com a parte padrao). Soma 100."""
    tot_p = sum(padrao.values()) or 1
    positivos = {k: max(0.0, v or 0.0) for k, v in excessos.items() if k in padrao}
    tot_e = sum(positivos.values())
    saida = {}
    for k, w in padrao.items():
        parte = w / tot_p
        saida[k] = 0.5 * parte + 0.5 * (positivos.get(k, 0.0) / tot_e) if tot_e > 0 else parte
    total = sum(saida.values()) or 1
    exatos = {k: 100 * v / total for k, v in saida.items()}
    inteiros = {k: int(v) for k, v in exatos.items()}
    for k in sorted(exatos, key=lambda k: -(exatos[k] - inteiros[k]))[:100 - sum(inteiros.values())]:
        inteiros[k] += 1          # maior resto: a soma fecha em 100
    return inteiros


def rodar(conn, cfg: dict | None = None, meses=MESES_INICIO) -> dict:
    lim = conn.execute("SELECT min(data), max(data) FROM cotacoes").fetchone()
    if not lim or not lim[0]:
        return {"periodos": [], "historico": {}}
    datas = datas_teste(lim[0], lim[1], meses)
    splits: dict[str, list] = {}
    for t, d, f in conn.execute("SELECT ticker, data, fator FROM desdobramentos WHERE fator > 0 ORDER BY data"):
        splits.setdefault(t, []).append((d, f))
    divs, _, _ = notas.carregar_proventos(conn)

    padrao = dict(notas.PADRAO["pesos"], **((cfg or {}).get("pesos") or {}))
    periodos, historico, guardados = [], {}, []
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
        por_metodo = {k: media([ret[r["t"]] for r in topo_por(ranking, lambda r, k=k: r["m"].get(k))]) for k in notas.METODOS}
        ref = retorno_acao(conn, REFERENCIA, inicio, fim, splits.get(REFERENCIA, []), divs.get(REFERENCIA, []))
        guardados.append([(r["m"], ret[r["t"]]) for r in ranking])
        longo = sorted([r for r in ranking if r.get("lp") is not None and r["vol"] >= LIQUIDEZ_LONGO],
                       key=lambda r: (-r["lp"], -r["final"]))[:TOPO]
        periodos.append({
            "inicio": inicio, "fim": fim, "completo": fim == fim_alvo, "n": len(ranking),
            "top": media([ret[r["t"]] for r in top]),
            "longo": media([ret[r["t"]] for r in longo]) if len(longo) >= 5 else None,
            "universo": media(list(ret.values())),
            "cdi": retorno_cdi(conn, inicio, fim),
            "ibov": ref,
            "metodos": por_metodo,
            "acoesTop": [[r["t"], round(r["final"]), ret[r["t"]]] for r in top],
            "acoesLongo": [[r["t"], r["lp"], ret[r["t"]]] for r in longo],
        })

    # desempenho de cada metodo sozinho e pesos sugeridos
    fech = [i for i, p in enumerate(periodos) if p["completo"] and p["universo"] is not None]
    por_metodo = {}
    for k in notas.METODOS:
        exc = [periodos[i]["metodos"][k] - periodos[i]["universo"] for i in fech if periodos[i]["metodos"].get(k) is not None]
        por_metodo[k] = {"excesso": media(exc), "venceu": [sum(1 for e in exc if e > 0), len(exc)],
                         "medio": media([periodos[i]["metodos"][k] for i in fech])}
    sugeridos = sugerir_pesos({k: v["excesso"] for k, v in por_metodo.items()}, padrao)
    # pesos calibrados avaliados fora da amostra: em cada periodo, pesos tirados so dos outros periodos
    for i, p in enumerate(periodos):
        outros = [j for j in fech if j != i]
        exc = {k: media([periodos[j]["metodos"][k] - periodos[j]["universo"] for j in outros
                         if periodos[j]["metodos"].get(k) is not None]) for k in notas.METODOS}
        pesos_i = sugerir_pesos(exc, padrao) if outros else padrao
        notas_i = sorted(((notas.nota_final(m, pesos_i), r) for m, r in guardados[i]), key=lambda x: -(x[0] or -1))
        p["calibrado"] = media([r for v, r in notas_i[:TOPO] if v is not None])

    # carteira trocada uma vez por ano (so os periodos de maio completos, que nao se sobrepoem)
    anuais = [p for p in periodos if p["completo"] and p["inicio"][5:7] == f"{meses[0]:02d}"]
    acumulado = {k: encadear([p[k] for p in anuais]) for k in ("top", "calibrado", "universo", "cdi", "ibov")} if anuais else None
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
        "porMetodo": por_metodo,
        "pesosPadrao": padrao,
        "pesosSugeridos": sugeridos,
        "venceuCalibrado": [sum(1 for i in fech if periodos[i].get("calibrado") is not None and periodos[i]["calibrado"] > periodos[i]["universo"]), len(fech)],
        "anosAcumulados": len(anuais),
        "venceuUniverso": [sum(1 for p in completos if p["top"] > p["universo"]), len(completos)],
        "venceuLongo": [sum(1 for p in completos if p["longo"] is not None and p["longo"] > p["universo"]),
                        sum(1 for p in completos if p["longo"] is not None)],
        "historico": historico,
    }

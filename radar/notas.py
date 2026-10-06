"""Etapa 2: indicadores e notas de 0 a 100 por metodo.

As notas saem de regras fixas aplicadas aos dados coletados. Dado ausente vira None:
o metodo fica fora da media e a confianca do ativo cai. Nada e estimado.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, timedelta

PADRAO = {
    "volume_minimo": 500000,          # R$/dia para entrar no ranking
    "pesos": {"barsi": 20, "bazin": 15, "qualidade": 30, "graham": 15, "greenblatt": 20},
    "dy_minimo": 0.06,
    "roe_minimo": 0.15,
    "divida_ebit_max": 3.0,
    "liquidez_boa": 2000000,
    "crescimento_receita_min": 0.05,
    "nucleo_cripto": ["bitcoin", "ethereum"],
}
METODOS = ["barsi", "bazin", "qualidade", "graham", "greenblatt"]


# ---------------------------------------------------------------- utilidades

def norm(txt) -> str:
    s = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"\s+", " ", s)


def meses(ini: str, fim: str) -> int:
    return round(((date.fromisoformat(fim) - date.fromisoformat(ini)).days + 1) / 30.44)


def limitar(v: float, a: float = 0.0, b: float = 100.0) -> float:
    return max(a, min(b, v))


def nivel(conta: str) -> int:
    return conta.count(".") + 1


def classificar_setor(setor_cvm: str) -> tuple[str, bool, bool]:
    """Devolve (rotulo, esta nos setores BESST, usa plano de contas financeiro)."""
    n = norm(setor_cvm)
    rotulo = re.sub(r"^Emp\. Adm\. Part\.\s*-\s*", "", setor_cvm or "").strip()
    rotulo = re.split(r"[(,]", rotulo)[0].strip()               # "Comércio (Atacado e Varejo)" vira "Comércio"
    if not rotulo or norm(rotulo).startswith("sem setor"):
        rotulo = "Sem setor"
    if "banco" in n or "intermediacao financeira" in n or "arrendamento mercantil" in n:
        return "Bancos", True, True
    if "segur" in n or "previdencia" in n:
        return "Seguros", True, True
    if "energia eletrica" in n:
        return "Energia elétrica", True, False
    if "saneamento" in n:
        return "Saneamento", True, False
    if "telecom" in n:
        return "Telecomunicações", True, False
    return rotulo, False, False


# ---------------------------------------------------------------- demonstrativos

def montar_fundamentos(linhas) -> dict:
    """linhas: (demonstrativo, dt_refer, dt_ini, dt_fim, cd_conta, ds_conta, valor) de UMA empresa.

    Localiza as contas pela descricao, porque o codigo muda entre o plano de contas
    comum e o de bancos e seguradoras.
    """
    dre: dict[tuple, list] = {}
    bpp: dict[str, list] = {}
    bpa: dict[str, list] = {}
    for dem, refer, ini, fim, cd, ds, valor in linhas:
        if valor is None:
            continue
        item = (cd, norm(ds), valor)
        if dem == "DRE" and ini and fim:
            dre.setdefault((ini, fim), []).append(item)
        elif dem == "BPP":
            bpp.setdefault(refer, []).append(item)
        elif dem == "BPA":
            bpa.setdefault(refer, []).append(item)

    lucro, receita, ebit = {}, {}, {}
    for periodo, itens in dre.items():
        pais = [i for i in itens if nivel(i[0]) == 2 and i[1].startswith("lucro") and "prejuizo" in i[1] and "periodo" in i[1]]
        if pais:
            pai = max(pais, key=lambda i: i[0])
            filhos = [i for i in itens if i[0].startswith(pai[0] + ".") and "controladora" in i[1] and "nao control" not in i[1]]
            # ha empresas que deixam a linha da controladora zerada; nesse caso vale o total
            lucro[periodo] = filhos[0][2] if (filhos and filhos[0][2] != 0) else pai[2]
        for cd, n, valor in itens:
            if cd == "3.01":
                receita[periodo] = valor
            elif nivel(cd) == 2 and "antes do resultado financeiro" in n:
                ebit[periodo] = valor

    f = {"lucro": lucro, "receita": receita, "ebit": ebit, "plano_financeiro": bool(lucro) and not ebit}

    if bpp:
        refer = max(bpp)
        itens = bpp[refer]
        pais = [i for i in itens if nivel(i[0]) == 2 and i[1].startswith("patrimonio liquido")]
        if pais:
            pai = max(pais, key=lambda i: i[0])
            minor = sum(i[2] for i in itens if i[0].startswith(pai[0] + ".") and "nao controladores" in i[1])
            f["pl_total"], f["pl"], f["balanco_em"] = pai[2], pai[2] - minor, refer
        dividas = [i[2] for i in itens if nivel(i[0]) == 3 and i[1].startswith("emprestimos e financiamentos")]
        f["divida"] = sum(dividas) if dividas else None
    if bpa:
        itens = bpa[max(bpa)]
        caixa = [i[2] for i in itens if i[0] in ("1.01.01", "1.01.02") and ("caixa" in i[1] or "aplicac" in i[1])]
        f["caixa"] = sum(caixa) if caixa else None
    return f


def trimestres(fluxo: dict) -> dict[str, float]:
    """De {(inicio, fim): valor} para {fim_do_trimestre: valor do trimestre isolado}.
    O quarto trimestre sai do ano inteiro menos os nove meses acumulados."""
    q = {fim: v for (ini, fim), v in fluxo.items() if meses(ini, fim) == 3}
    por_inicio: dict[str, dict[int, tuple]] = {}
    for (ini, fim), v in fluxo.items():
        por_inicio.setdefault(ini, {})[meses(ini, fim)] = (fim, v)
    for acumulados in por_inicio.values():
        for m in (6, 9, 12):
            if m in acumulados and (m - 3) in acumulados and acumulados[m][0] not in q:
                q[acumulados[m][0]] = acumulados[m][1] - acumulados[m - 3][1]
    return q


def anuais(fluxo: dict) -> dict[str, float]:
    return {fim: v for (ini, fim), v in fluxo.items() if meses(ini, fim) == 12}


def ultimos_12_meses(fluxo: dict) -> tuple[float | None, str | None, str | None]:
    """Soma dos 4 ultimos trimestres seguidos; se faltar algum, o ultimo ano fechado."""
    q = trimestres(fluxo)
    fins = sorted(q, reverse=True)
    ano = anuais(fluxo)
    if len(fins) >= 4:
        seguidos = all(75 <= (date.fromisoformat(fins[i]) - date.fromisoformat(fins[i + 1])).days <= 100 for i in range(3))
        if seguidos and (not ano or fins[0] >= max(ano)):
            return sum(q[x] for x in fins[:4]), fins[0], "4 trimestres"
    if ano:
        fim = max(ano)
        return ano[fim], fim, "ultimo ano"
    return None, None, None


# ---------------------------------------------------------------- notas (funcoes puras)

def nota_margem(margem: float) -> float:
    """Margem de 0% vale 50; +50% vale 100; -50% vale 0."""
    return limitar(50 + margem * 100)


def nota_barsi(preco, dpa_medio, anos_pagos, besst, dy_minimo=0.06):
    """Preco-teto = dividendo medio por acao / 6%. Devolve (nota, teto, margem)."""
    if dpa_medio is None or not preco:
        return None, None, None
    if dpa_medio <= 0:
        return 0.0, None, None
    teto = dpa_medio / dy_minimo
    margem = (teto - preco) / teto
    nota = nota_margem(margem) - (20 if anos_pagos < 4 else 0) + (10 if besst else 0)
    return limitar(nota), teto, margem


def nota_bazin(dy12, anos_pagos, divida_sobre_pl, financeiro, dy_minimo=0.06):
    """Dividendos de 12 meses sobre o preco: 6% vale 60 e 10% vale 100."""
    if dy12 is None:
        return None
    nota = 60 + (dy12 - dy_minimo) * 1000 if dy12 >= dy_minimo else dy12 / dy_minimo * 60
    if anos_pagos < 5:
        nota *= 0.7
    if not financeiro and divida_sobre_pl is not None and divida_sobre_pl > 1:
        nota -= 15
    return limitar(nota)


def nota_graham(preco, p_l, p_vp):
    """Valor de Graham = raiz(22,5 x LPA x VPA), escrito com P/L e P/VP. Devolve (nota, valor, margem)."""
    if p_l is None or p_vp is None or not preco:
        return None, None, None
    if p_l <= 0 or p_vp <= 0:
        return 0.0, None, None
    valor = preco * math.sqrt(22.5 / (p_l * p_vp))
    margem = (valor - preco) / valor
    return nota_margem(margem), valor, margem


def nota_checklist(itens: list[tuple[str, bool | None]]) -> float | None:
    validos = [ok for _, ok in itens if ok is not None]
    if len(validos) < 5:
        return None
    return 100.0 * sum(1 for ok in validos if ok) / len(validos)


def notas_greenblatt(empresas: dict[str, tuple[float, float]]) -> dict[str, float]:
    """empresas: {chave: (lucro operacional / valor da firma, lucro operacional / capital)}.
    Soma a posicao nos dois rankings; a melhor soma leva 100 e a pior leva 10.
    Quem tem lucro operacional negativo leva 0."""
    positivos = {k: v for k, v in empresas.items() if v[0] > 0 and v[1] > 0}
    saida = {k: 0.0 for k in empresas if k not in positivos}
    if not positivos:
        return saida
    por_ey = {k: i for i, k in enumerate(sorted(positivos, key=lambda k: -positivos[k][0]))}
    por_roc = {k: i for i, k in enumerate(sorted(positivos, key=lambda k: -positivos[k][1]))}
    ordem = sorted(positivos, key=lambda k: (por_ey[k] + por_roc[k], k))
    n = len(ordem)
    for pos, k in enumerate(ordem):
        saida[k] = 100.0 if n == 1 else 10 + 90 * (1 - pos / (n - 1))
    return saida


def nota_final(notas: dict, pesos: dict) -> float | None:
    soma = peso = 0.0
    for metodo, p in pesos.items():
        if notas.get(metodo) is not None:
            soma += notas[metodo] * p
            peso += p
    return soma / peso if peso else None


# ---------------------------------------------------------------- acoes

def _classe(ticker: str) -> str:
    sufixo = ticker[4:]
    return "on" if sufixo == "3" else "unit" if sufixo == "11" else "pn"


def calcular_acoes(conn, cfg: dict | None = None) -> list[dict]:
    p = dict(PADRAO, **(cfg or {}))
    pesos = p["pesos"]
    ultima = conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0]
    if not ultima:
        return []
    hoje = date.fromisoformat(ultima)
    desde = (hoje - timedelta(days=400)).isoformat()

    # ----- mercado: preco, volume e serie por ticker
    mercado: dict[str, dict] = {}
    for ticker, dia, fech, vol in conn.execute(
            """SELECT c.ticker, c.data, c.fechamento, c.volume FROM cotacoes c JOIN ativos a ON a.ticker = c.ticker
               WHERE a.tipo IN ('acao', 'unit') AND c.data >= ? ORDER BY c.ticker, c.data""", (desde,)):
        mercado.setdefault(ticker, {"dias": []})["dias"].append((dia, fech, vol or 0.0))
    desdobros: dict[str, list] = {}
    for ticker, dia, fator in conn.execute("SELECT ticker, data, fator FROM desdobramentos WHERE fator > 0 ORDER BY data"):
        desdobros.setdefault(ticker, []).append((dia, fator))

    def fator_depois(ticker: str, dia: str) -> float:
        """Quantas acoes de hoje correspondem a uma acao naquela data."""
        f = 1.0
        for quando, fator in desdobros.get(ticker, ()):
            if quando > dia:
                f *= fator
        return f

    for ticker, m in mercado.items():
        if ticker in desdobros:   # preco antigo na base de hoje: divide pelo fator dos desdobramentos posteriores
            m["dias"] = [(d, fech / fator_depois(ticker, d), v) for d, fech, v in m["dias"]]
    pregoes = [d for (d,) in conn.execute("SELECT DISTINCT data FROM cotacoes WHERE data >= ? ORDER BY data DESC LIMIT 60", (desde,))]
    corte60 = pregoes[-1] if pregoes else ultima
    recente = (hoje - timedelta(days=7)).isoformat()
    for ticker, m in mercado.items():
        dias = m["dias"]
        m["preco"], m["data"] = dias[-1][1], dias[-1][0]
        m["volume"] = sum(v for d, _, v in dias if d >= corte60) / max(1, len(pregoes))
        alvo = (hoje - timedelta(days=365)).isoformat()
        antigos = [f for d, f, _ in dias if d <= alvo]
        m["var12"] = (m["preco"] / antigos[-1] - 1) if antigos and antigos[-1] else None
        ano = [f for d, f, _ in dias if d > alvo]
        passo = max(1, len(ano) // 52)
        serie = ano[::-1][::passo][::-1]
        m["serie"] = [round(x, 2) for x in serie[-53:]]
        m["ativo"] = m["data"] >= recente

    # ----- empresas e seus tickers
    empresas: dict[str, dict] = {}
    for ticker, cnpj, nome_pregao in conn.execute(
            "SELECT ticker, cnpj, nome_pregao FROM ativos WHERE tipo IN ('acao', 'unit') AND cnpj IS NOT NULL"):
        if ticker in mercado and mercado[ticker]["ativo"]:
            e = empresas.setdefault(cnpj, {"tickers": [], "nome": nome_pregao})
            e["tickers"].append(ticker)
    for cnpj, nome, setor in conn.execute("SELECT cnpj, nome, setor FROM empresas"):
        if cnpj in empresas:
            empresas[cnpj]["razao"], empresas[cnpj]["setor_cvm"] = nome, setor or ""

    # ----- demonstrativos
    contas: dict[str, list] = {}
    for cnpj, dem, refer, ini, fim, cd, ds, valor in conn.execute(
            """SELECT cnpj, demonstrativo, dt_refer, dt_ini_exerc, dt_fim_exerc, cd_conta, ds_conta, valor
               FROM demonstrativos WHERE demonstrativo IN ('DRE', 'BPA', 'BPP')"""):
        if cnpj in empresas:
            contas.setdefault(cnpj, []).append((dem, refer, ini, fim, cd, ds, valor))
    capital = {}
    for cnpj, refer, on_t, pn_t, tot, on_x, pn_x, tes in conn.execute(
            "SELECT cnpj, dt_refer, on_total, pn_total, total, on_tes, pn_tes, tes FROM capital ORDER BY dt_refer"):
        capital[cnpj] = (refer, on_t or 0.0, pn_t or 0.0, tot or 0.0, on_x or 0.0, pn_x or 0.0, tes or 0.0)

    # ----- dividendos por ticker
    divs: dict[str, list] = {}
    for ticker, dia, valor in conn.execute("SELECT ticker, data, valor FROM dividendos ORDER BY data"):
        divs.setdefault(ticker, []).append((dia, valor))
    consultados = {t for (t,) in conn.execute("SELECT ticker FROM dividendos_controle WHERE situacao LIKE 'ok%'")}

    resultado, gb_entrada = [], {}
    for cnpj, e in empresas.items():
        ticker = max(e["tickers"], key=lambda t: mercado[t]["volume"])
        m = mercado[ticker]
        if m["volume"] < p["volume_minimo"]:
            continue
        setor, besst, financeiro = classificar_setor(e.get("setor_cvm", ""))
        f = montar_fundamentos(contas.get(cnpj, []))
        financeiro = financeiro or f.get("plano_financeiro", False)
        preco = m["preco"]

        lucro12, lucro_fim, lucro_origem = ultimos_12_meses(f["lucro"])
        receita12, _, _ = ultimos_12_meses(f["receita"])
        ebit12, _, _ = ultimos_12_meses(f["ebit"])
        pl = f.get("pl")
        divida, caixa = f.get("divida"), f.get("caixa")
        divida_liq = (divida - (caixa or 0.0)) if divida is not None else None

        # valor de mercado: acoes de cada classe x preco da classe.
        # P/L e P/VP seguem a convencao usual (preco deste papel x todas as acoes); units usam o valor por classe.
        valor_mercado = valor_papel = None
        cap = capital.get(cnpj)
        if cap:
            cap_em, on_t, pn_t, tot, on_x, pn_x, tes = cap
            ajuste = fator_depois(ticker, cap_em)          # desdobramento depois do ultimo balanco
            on_t, pn_t, tot, on_x, pn_x, tes = (x * ajuste for x in (on_t, pn_t, tot, on_x, pn_x, tes))
            q_on, q_pn = on_t - on_x, pn_t - pn_x
            q_total = (q_on + q_pn) or (tot - tes)
            precos = {_classe(t): mercado[t]["preco"] for t in sorted(e["tickers"], key=lambda t: mercado[t]["volume"])}
            p_on = precos.get("on") or precos.get("pn")
            p_pn = precos.get("pn") or precos.get("on")
            if q_total > 0 and p_on:
                valor_mercado = (q_on * p_on + q_pn * p_pn) if (q_on + q_pn) > 0 else q_total * p_on
                valor_papel = valor_mercado if _classe(ticker) == "unit" else preco * q_total
                if pl and pl > 0:
                    fator = 1000 if valor_mercado / pl < 0.02 else 1          # quantidade informada em milhares
                    valor_mercado, valor_papel = valor_mercado * fator, valor_papel * fator
                    if not (0.02 <= valor_mercado / pl <= 200):
                        valor_mercado = valor_papel = None
        p_l = (valor_papel / lucro12) if (valor_papel and lucro12) else None
        p_vp = (valor_papel / pl) if (valor_papel and pl) else None

        # lucro muito acima do historico costuma ter item que nao se repete: Graham e Greenblatt usam a media de 3 anos
        def normal(valor12, serie_anual):
            ult = [serie_anual[a] for a in sorted(serie_anual)[-3:]]
            if valor12 and len(ult) == 3 and all(x > 0 for x in ult) and valor12 > 2 * (sum(ult) / 3):
                return sum(ult) / 3, True
            return valor12, False
        lucro_base, lucro_fora = normal(lucro12, anuais(f["lucro"]))
        ebit_base, ebit_fora = normal(ebit12, anuais(f["ebit"]))
        p_l_base = (valor_papel / lucro_base) if (valor_papel and lucro_base) else None
        roe = (lucro12 / pl) if (lucro12 is not None and pl and pl > 0) else None

        # dividendos
        tem_div = ticker in consultados
        pagos = divs.get(ticker, [])
        janelas = []
        for k in range(5):
            a = (hoje - timedelta(days=365 * (k + 1))).isoformat()
            b = (hoje - timedelta(days=365 * k)).isoformat()
            janelas.append(sum(v for d, v in pagos if a < d <= b))
        dpa12 = janelas[0] if tem_div else None
        dpa_medio = sorted(janelas)[2] if tem_div else None      # mediana de 5 anos: ignora pagamento fora da curva
        anos_pagos = sum(1 for j in janelas if j > 0)
        dy12 = (dpa12 / preco) if (dpa12 is not None and preco) else None
        dy_tipico = (dpa_medio / preco) if (tem_div and preco) else None
        dy_bazin = min(dy12, dy_tipico) if dy12 is not None else None  # pagamento extraordinario nao infla a nota

        # ----- notas
        notas, nulo = {}, {}
        notas["barsi"], teto, margem_teto = nota_barsi(preco, dpa_medio, anos_pagos, besst, p["dy_minimo"])
        divida_pl = (divida_liq / pl) if (divida_liq is not None and pl and pl > 0) else None
        notas["bazin"] = nota_bazin(dy_bazin, anos_pagos, divida_pl, financeiro, p["dy_minimo"])
        if not tem_div:
            nulo["barsi"] = nulo["bazin"] = "Sem histórico de dividendos na coleta."
        notas["graham"], graham, margem_graham = nota_graham(preco, p_l_base, p_vp)
        if notas["graham"] is None:
            nulo["graham"] = "Falta lucro, patrimônio ou quantidade de ações."

        lucros_ano = anuais(f["lucro"])
        receitas_ano = anuais(f["receita"])
        anos = sorted(lucros_ano)
        ultimos5 = anos[-5:]
        tri = trimestres(f["lucro"])
        tri_fins = sorted(tri)[-20:]
        cresc_receita = None
        r_anos = sorted(receitas_ano)
        if len(r_anos) >= 5 and receitas_ano[r_anos[-5]] > 0 and receitas_ano[r_anos[-1]] > 0:
            cresc_receita = (receitas_ano[r_anos[-1]] / receitas_ano[r_anos[-5]]) ** (1 / 4) - 1
        divida_ebit = (divida_liq / ebit12) if (divida_liq is not None and ebit12 and ebit12 > 0) else None
        check = [
            (f"Retorno sobre o patrimônio de {p['roe_minimo']:.0%} ou mais", None if roe is None else roe >= p["roe_minimo"]),
            ("Lucro em cada um dos últimos 5 anos", None if len(ultimos5) < 5 else all(lucros_ano[a] > 0 for a in ultimos5)),
            ("Lucro em todos os trimestres dos últimos 5 anos", None if len(tri_fins) < 12 else all(tri[x] > 0 for x in tri_fins)),
            ("Receita cresceu 5% ao ano ou mais em 5 anos", None if financeiro or cresc_receita is None else cresc_receita >= p["crescimento_receita_min"]),
            ("Lucro maior que o de 5 anos atrás", None if len(ultimos5) < 5 else lucros_ano[ultimos5[-1]] > lucros_ano[ultimos5[0]] > 0),
            (f"Dívida líquida de até {p['divida_ebit_max']:.0f} vezes o lucro operacional",
             None if financeiro else (True if (divida_liq is not None and divida_liq <= 0) else
                                      None if divida_ebit is None else divida_ebit <= p["divida_ebit_max"])),
            ("Pelo menos 5 anos de balanços publicados", len(anos) >= 5 if anos else None),
            ("Volume médio acima de R$ 2 milhões por dia", m["volume"] >= p["liquidez_boa"]),
        ]
        notas["qualidade"] = nota_checklist(check)
        if notas["qualidade"] is None:
            nulo["qualidade"] = "Menos de 5 itens do checklist com dados."

        if financeiro:
            notas["greenblatt"] = None
            nulo["greenblatt"] = "Não se aplica a bancos e seguradoras."
        elif ebit12 is None or valor_mercado is None or divida_liq is None or not f.get("pl_total"):
            notas["greenblatt"] = None
            nulo["greenblatt"] = "Falta lucro operacional, dívida ou valor de mercado."
        else:
            firma = valor_mercado + divida_liq
            capital_inv = f["pl_total"] + divida_liq
            if firma > 0 and capital_inv > 0:
                gb_entrada[ticker] = (ebit_base / firma, ebit_base / capital_inv)
            else:
                notas["greenblatt"] = None
                nulo["greenblatt"] = "Valor da firma ou capital investido negativo."

        # ----- alertas
        alertas = []
        if lucro12 is not None and lucro12 < 0:
            alertas.append("Prejuízo nos últimos 12 meses")
        if pl is not None and pl <= 0:
            alertas.append("Patrimônio líquido negativo")
        if len(ultimos5) >= 3 and any(lucros_ano[a] < 0 for a in ultimos5) and not (lucro12 is not None and lucro12 < 0):
            alertas.append("Teve prejuízo em pelo menos um dos últimos 5 anos")
        if divida_ebit is not None and divida_ebit > p["divida_ebit_max"]:
            alertas.append(f"Dívida líquida de {divida_ebit:.1f} vezes o lucro operacional".replace(".", ","))
        if lucro_fora or ebit_fora:
            alertas.append("Lucro de 12 meses é mais que o dobro da média de 3 anos: Graham e Greenblatt usam a média")
        if m["volume"] < p["liquidez_boa"]:
            alertas.append("Pouca liquidez: menos de R$ 2 milhões negociados por dia")
        if f.get("balanco_em") and (hoje - date.fromisoformat(f["balanco_em"])).days > 270:
            alertas.append("Último balanço tem mais de 9 meses")
        if dy12 is not None and dy12 > 0.15:
            alertas.append(f"Dividendos de 12 meses somam {dy12:.0%} do preço: pode incluir pagamento extraordinário")
        if m["var12"] is not None and m["var12"] <= -0.30:
            alertas.append(f"Caiu {abs(m['var12']):.0%} em 12 meses")

        dados = [preco, m["volume"] > 0, lucro12, pl, valor_mercado, dpa12, f.get("balanco_em"),
                 True if financeiro else ebit12, True if financeiro else divida, len(anos) >= 5 or None]
        resultado.append({
            "t": ticker, "n": e["nome"], "razao": e.get("razao"), "s": setor, "p": round(preco, 2), "data": m["data"],
            "vol": round(m["volume"]), "var12": m["var12"], "m": notas, "nulo": nulo,
            "teto": teto, "margemTeto": margem_teto, "graham": graham, "margemGraham": margem_graham,
            "dy": dy12, "dyTipico": dy_tipico, "dpa": dpa_medio, "anosDiv": anos_pagos if tem_div else None,
            "pl": p_l, "pvp": p_vp, "roe": roe, "divEbit": divida_ebit, "valorMercado": valor_mercado,
            "check": [[txt, ok] for txt, ok in check], "al": alertas,
            "conf": [sum(1 for d in dados if d is not None and d is not False), len(dados)],
            "serie": m["serie"], "lucroAte": lucro_fim, "lucroBase": lucro_origem, "balancoEm": f.get("balanco_em"),
            "aplicaveis": 4 if financeiro else 5, "lucroNormalizado": lucro_fora or ebit_fora,
        })

    gb = notas_greenblatt(gb_entrada)
    for r in resultado:
        if r["t"] in gb:
            r["m"]["greenblatt"] = gb[r["t"]]
        r["final"] = nota_final(r["m"], pesos)
        r["m"] = {k: (None if r["m"].get(k) is None else round(r["m"][k])) for k in METODOS}
    resultado = [r for r in resultado if r["final"] is not None]
    resultado.sort(key=lambda r: -r["final"])
    return resultado


# ---------------------------------------------------------------- cripto

def nota_cripto(acima_media_pct: float | None, volatilidade_pct: float | None, posicao: int | None) -> float | None:
    """45% tendencia (preco contra a media de 200 dias), 30% risco (volatilidade) e 25% tamanho."""
    if acima_media_pct is None or volatilidade_pct is None:
        return None
    tendencia = limitar(50 + acima_media_pct * 1.5)
    risco = limitar(100 - volatilidade_pct)
    tamanho = limitar(100 - ((posicao or 50) - 1) * 3, 10, 100)
    return 0.45 * tendencia + 0.30 * risco + 0.25 * tamanho


def calcular_cripto(conn, cfg: dict | None = None) -> list[dict]:
    p = dict(PADRAO, **(cfg or {}))
    series: dict[str, list] = {}
    for cid, dia, preco in conn.execute("SELECT id, data, preco FROM cripto_cotacoes ORDER BY id, data"):
        if preco:
            series.setdefault(cid, []).append((dia, preco))
    saida = []
    for cid, simbolo, nome, posicao, preco, cap, vol24, maxima, dist, _ in conn.execute(
            """SELECT id, simbolo, nome, posicao, preco, valor_mercado, volume_24h, maxima_historica,
                      dist_maxima_pct, data_maxima FROM cripto_ativos WHERE no_universo = 1 ORDER BY posicao"""):
        s = [x for _, x in series.get(cid, [])]
        media = (sum(s[-200:]) / 200) if len(s) >= 200 else None
        acima = ((s[-1] / media - 1) * 100) if media else None
        volat = None
        if len(s) >= 31:
            ret = [math.log(s[i] / s[i - 1]) for i in range(len(s) - 30, len(s)) if s[i - 1] > 0 and s[i] > 0]
            if len(ret) >= 20:
                med = sum(ret) / len(ret)
                volat = math.sqrt(sum((r - med) ** 2 for r in ret) / (len(ret) - 1)) * math.sqrt(365) * 100
        nota = nota_cripto(acima, volat, posicao)
        alertas = []
        if acima is not None and acima < 0:
            alertas.append("Abaixo da média de 200 dias")
        if volat is not None and volat > 100:
            alertas.append("Volatilidade muito alta")
        if media is None:
            alertas.append("Menos de 200 dias de histórico")
        passo = max(1, len(s) // 52)
        saida.append({
            "id": (simbolo or cid).upper(), "n": nome, "nucleo": cid in p["nucleo_cripto"], "p": preco,
            "pos": posicao, "ma": acima, "max": dist, "vol": volat, "nota": None if nota is None else round(nota),
            "al": alertas, "serie": s[::-1][::passo][::-1][-53:], "valorMercado": cap,
        })
    saida.sort(key=lambda c: -(c["nota"] if c["nota"] is not None else -1))
    return saida

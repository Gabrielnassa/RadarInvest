"""Backtest, fundos imobiliarios, Tesouro, Ebitda e conferencia de dividendos."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from radar import alertas, backtest, db, exportar, notas, resumos


def _dias(inicio: str, fim: str, passo: int = 7) -> list[str]:
    d, f, saida = date.fromisoformat(inicio), date.fromisoformat(fim), []
    while d <= f:
        saida.append(d.isoformat())
        d += timedelta(days=passo)
    return saida


def _empresa(conn, n: int, preco, lucro: float = 100e6, setor: str = "Comércio", ultima: str = "2026-10-02", da: float = 0.0):
    """Empresa com 100 mi de acoes, cotacao semanal de 2024-01 ate `ultima` e 5 anos de balancos.
    `preco` e um numero ou uma funcao da data."""
    cnpj = f"{n:014d}"
    t = f"EMP{chr(65 + n)}3"
    conn.execute("INSERT INTO empresas (cnpj, nome, setor) VALUES (?, ?, ?)", (cnpj, f"EMPRESA {n} S.A.", setor))
    conn.execute("INSERT INTO ativos (ticker, tipo, nome_pregao, ultima_data, cnpj) VALUES (?, 'acao', ?, ?, ?)", (t, f"EMP{n}", ultima, cnpj))
    dias = _dias("2024-01-05", ultima) + [ultima]
    conn.executemany("INSERT OR REPLACE INTO cotacoes (ticker, data, fechamento, volume) VALUES (?, ?, ?, 5000000)",
                     [(t, d, preco(d) if callable(preco) else preco) for d in dias])
    conn.execute("INSERT INTO capital (cnpj, dt_refer, on_total, pn_total, total, on_tes, pn_tes, tes) VALUES (?, '2021-12-31', 100e6, 0, 100e6, 0, 0, 0)", (cnpj,))
    for ano in range(2020, 2026):
        for cd, ds, v in (("3.01", "Receita", 1e9), ("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", lucro * 1.5),
                          ("3.11", "Lucro/Prejuízo do Período", lucro)):
            conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'DRE', 1, ?, ?, ?, 1, ?, ?, ?)",
                         (cnpj, f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", cd, ds, v))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'BPP', 1, ?, '', ?, 1, '2.03', 'Patrimônio Líquido', 1e9)",
                     (cnpj, f"{ano}-12-31", f"{ano}-12-31"))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'BPP', 1, ?, '', ?, 1, '2.01.04', 'Empréstimos e Financiamentos', 450e6)",
                     (cnpj, f"{ano}-12-31", f"{ano}-12-31"))
        if da:
            conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'DFC_MI', 1, ?, ?, ?, 1, '6.01.01.02', 'Depreciação e Amortização', ?)",
                         (cnpj, f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", da))
    return t, cnpj


class TestFluxoDeCaixa(unittest.TestCase):
    def test_depreciacao_e_dividendos_pagos(self):
        p = ("2025-01-01", "2025-12-31")
        linhas = [
            ("DFC_MI", "2025-12-31", *p, "6.01.01.02", "Depreciação, Amortização e Exaustão", 80.0),
            ("DFC_MI", "2025-12-31", *p, "6.01.01.03", "Depreciação de Arrendamentos", 5.0),
            ("DFC_MI", "2025-12-31", *p, "6.01.01.04", "Provisões", 9.0),
            ("DFC_MI", "2025-12-31", *p, "6.03.04", "Dividendos Pagos", -30.0),
            ("DFC_MI", "2025-12-31", *p, "6.03.05", "Juros sobre o Capital Próprio Pagos", -10.0),
            ("DFC_MI", "2025-12-31", *p, "6.03.06", "Dividendos Recebidos", 3.0),
        ]
        f = notas.montar_fundamentos(linhas)
        self.assertEqual(f["da"][p], 80.0)            # arrendamento e provisao ficam de fora
        self.assertEqual(f["div_pagos"][p], 40.0)

    def test_divida_sobre_ebitda(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        _empresa(conn, 1, 20.0, da=50e6)
        r = notas.calcular_acoes(conn)[0]
        # divida 450 mi / (lucro operacional 150 mi + depreciacao 50 mi) = 2,25
        self.assertEqual(r["baseDivida"], "Ebitda")
        self.assertAlmostEqual(r["divEbit"], 2.25)
        sem = db.conectar(":memory:")
        self.addCleanup(sem.close)
        _empresa(sem, 1, 20.0)
        r = notas.calcular_acoes(sem)[0]
        self.assertEqual(r["baseDivida"], "lucro operacional")
        self.assertAlmostEqual(r["divEbit"], 3.0)

    def test_conferencia_de_dividendos(self):
        self.assertAlmostEqual(notas.conferir_dividendos(2.0, 100e6, 200e6), 1.0)
        self.assertAlmostEqual(notas.conferir_dividendos(4.0, 100e6, 200e6), 2.0)
        self.assertIsNone(notas.conferir_dividendos(2.0, 100e6, 0))
        self.assertIsNone(notas.conferir_dividendos(None, 100e6, 200e6))

    def test_retorno_com_dividendos(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        t, _ = _empresa(conn, 1, lambda d: 10.0 if d < "2026-01-01" else 11.0)
        conn.execute("INSERT INTO dividendos VALUES (?, '2026-05-10', 0.5, 'yahoo')", (t,))
        conn.execute("INSERT INTO dividendos_controle VALUES (?, '2026-10-01', 'ok-2')", (t,))
        r = notas.calcular_acoes(conn)[0]
        self.assertAlmostEqual(r["var12"], 0.10)
        self.assertAlmostEqual(r["tr12"], 0.15)       # (11 + 0,50) / 10 - 1
        self.assertEqual(r["anual"]["anos"][-1], "2025")
        self.assertEqual(r["anual"]["lucro"][-1], 100e6)


class TestHistoricoDeCodigos(unittest.TestCase):
    def test_codigo_encerrado_liga_pela_tabela_historica(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.executemany("INSERT INTO ativos (ticker, tipo) VALUES (?, 'acao')", [("SAIU3",), ("DUPL3",)])
        conn.executemany("INSERT INTO empresa_tickers_hist VALUES (?, ?, NULL, '2023-05-01')",
                         [("SAIU3", "111"), ("DUPL3", "222"), ("DUPL3", "333")])
        db.vincular_ativos(conn)
        r = dict((t, (c, v)) for t, c, v in conn.execute("SELECT ticker, cnpj, vinculo FROM ativos"))
        self.assertEqual(r["SAIU3"], ("111", "historico"))
        self.assertEqual(r["DUPL3"], (None, None))          # codigo que passou por duas empresas fica sem vinculo


class TestProventos(unittest.TestCase):
    def test_b3_tem_preferencia_e_e_ajustada(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO ativos (ticker, tipo) VALUES ('AAAA3', 'acao'), ('BBBB3', 'acao')")
        # B3: 2,00 anunciado antes de um desdobramento de 1 para 2 => 1,00 na base de hoje
        conn.execute("INSERT INTO proventos_b3 VALUES ('AAAA3', '2025-03-10', 'DIVIDENDO', 2.0, '2025-04-01', NULL)")
        conn.execute("INSERT INTO desdobramentos VALUES ('AAAA3', '2025-06-01', 2.0, 'yahoo')")
        conn.execute("INSERT INTO dividendos VALUES ('AAAA3', '2025-03-11', 0.9, 'yahoo')")
        conn.execute("INSERT INTO dividendos VALUES ('BBBB3', '2025-03-11', 0.5, 'yahoo')")
        conn.execute("INSERT INTO proventos_b3_controle VALUES ('AAAA', '2026-10-01', 'ok')")
        divs, consultados, fonte = notas.carregar_proventos(conn)
        self.assertEqual(divs["AAAA3"], [("2025-03-10", 1.0)])          # Yahoo do mesmo periodo fica de fora
        self.assertEqual(divs["BBBB3"], [("2025-03-11", 0.5)])
        self.assertEqual((fonte["AAAA3"], fonte["BBBB3"]), ("B3", "Yahoo"))
        self.assertIn("AAAA3", consultados)
        self.assertEqual(notas.carregar_proventos(conn, "2025-01-01")[0], {})
        # B3 so traz os anos recentes: o Yahoo completa o historico anterior
        conn.execute("INSERT INTO dividendos VALUES ('AAAA3', '2022-05-11', 0.7, 'yahoo')")
        divs, _, fonte = notas.carregar_proventos(conn)
        self.assertEqual(divs["AAAA3"], [("2022-05-11", 0.7), ("2025-03-10", 1.0)])
        self.assertEqual(fonte["AAAA3"], "B3 e Yahoo")


class TestPublicacao(unittest.TestCase):
    def _dados(self, n=40, preco=10.0):
        return {"dataCotacao": "2026-10-06", "acoes": [{"t": f"A{i:03d}3", "p": preco, "final": 50, "lp": 60, "precos5": [["2026-09", 10]],
                                                         "hist": [], "anual": None} for i in range(n)], "fiis": [{"t": "F11", "nota": 70}]}

    def test_validar(self):
        self.assertEqual(exportar.validar(self._dados(), None), [])
        self.assertTrue(exportar.validar(self._dados(n=10), None))                     # poucas acoes
        self.assertTrue(exportar.validar(self._dados(n=40), self._dados(n=100)))        # queda forte
        self.assertTrue(exportar.validar(self._dados(preco=0), None))                   # preco zerado
        velho = self._dados(); velho["dataCotacao"] = "2026-10-07"
        self.assertTrue(exportar.validar(self._dados(), velho))                         # data voltou
        caiu = self._dados()
        for x in caiu["acoes"]:
            x["final"] = 20
        self.assertTrue(exportar.validar(caiu, self._dados()))                          # notas despencaram

    def test_separar_e_historico(self):
        d = self._dados(n=2)
        d["fatos"] = [{"t": "A0003"}]
        d["acoes"][0]["check"] = [["Lucro", True], ["Divida", None]]
        det = exportar.separar(d)
        self.assertEqual(det["_fatos"], [{"t": "A0003"}])
        self.assertEqual(d["rotulos_check"], ["Lucro", "Divida"])
        self.assertEqual(d["acoes"][0]["check"], [[0, True], [1, None]])
        self.assertEqual(det["A0003"]["precos5"], [["2026-09", 10]])
        self.assertNotIn("precos5", d["acoes"][0])
        with tempfile.TemporaryDirectory() as tmp:
            exportar.gravar_historico(Path(tmp), d)
            foto = json.loads((Path(tmp) / "2026-10-06.json").read_text())
            self.assertEqual(foto["acoes"][0], ["A0003", 50, 60])
            self.assertEqual(json.loads((Path(tmp) / "indice.json").read_text()), ["2026-10-06"])

    def test_prazo_do_balanco(self):
        self.assertEqual(exportar.prazo_balanco("2026-06-30"), ("2026-09-30", "2026-11-14"))
        self.assertEqual(exportar.prazo_balanco("2026-09-30"), ("2026-12-31", "2027-03-31"))
        self.assertEqual(exportar.prazo_balanco(None), (None, None))


class _Bloco:
    def __init__(self, texto):
        self.type, self.text = "text", texto


class _ClienteFalso:
    """Imita client.beta.messages.create e conta as chamadas."""
    def __init__(self):
        self.chamadas = []
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.chamadas.append(kw)
        r = type("R", (), {})()
        r.stop_reason, r.content = "end_turn", [_Bloco("Resumo de teste.")]
        return r


class TestOpcionais(unittest.TestCase):
    def test_resumos_reaproveitam_quando_os_numeros_nao_mudam(self):
        dados = {"acoes": [{"t": "AAAA3", "n": "A", "s": "Bancos", "p": 10.0, "final": 80, "lp": 90, "m": {}, "al": []}]}
        cli = _ClienteFalso()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(resumos.aplicar(dados, Path(tmp), cli), 1)
            self.assertEqual(dados["acoes"][0]["resumo"], "Resumo de teste.")
            self.assertEqual(cli.chamadas[0]["model"], "claude-opus-5-5")
            self.assertEqual(cli.chamadas[0]["fallbacks"], "default")
            self.assertEqual(resumos.aplicar(dados, Path(tmp), cli), 0)       # mesmos numeros: sem chamada nova
            dados["acoes"][0]["p"] = 12.0
            self.assertEqual(resumos.aplicar(dados, Path(tmp), cli), 1)
        self.assertEqual(len(cli.chamadas), 2)

    def test_sem_chave_nao_faz_nada(self):
        import os
        antes = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertEqual(resumos.aplicar({"acoes": []}, Path("."), None), 0)
        finally:
            if antes:
                os.environ["ANTHROPIC_API_KEY"] = antes

    def test_mensagem_de_alerta(self):
        dados = {"dataCotacao": "2026-10-06", "acoes": [{"t": "BBSE3", "final": 62.4, "al": []}, {"t": "OUTR3", "final": 10, "al": []}],
                 "fatos": [{"t": "BBSE3", "data": "2026-10-06", "cat": "Fato Relevante", "assunto": "Novo acordo"}],
                 "coleta": [{"fonte": "b3", "situacao": "erro"}]}
        anterior = {"data": "2026-10-05", "acoes": [["BBSE3", 77, 100], ["OUTR3", 90, 50]]}
        texto = alertas.montar_mensagem(dados, anterior, ["BBSE3"])
        self.assertIn("BBSE3: nota 77 -> 62", texto)
        self.assertIn("fato relevante", texto)
        self.assertIn("Coleta com erro: b3", texto)
        self.assertNotIn("OUTR3", texto)
        self.assertEqual(alertas.montar_mensagem({"acoes": []}, None, []), "")


class TestMetodosNovos(unittest.TestCase):
    def test_banco(self):
        nota, justo, margem = notas.nota_banco(1.0, 0.21, 0.14)      # justo 1,5 => margem 1/3
        self.assertAlmostEqual(justo, 1.5)
        self.assertAlmostEqual(nota, 50 + 100 / 3)
        self.assertEqual(notas.nota_banco(1.0, -0.05)[0], 0.0)
        self.assertEqual(notas.nota_banco(None, 0.2), (None, None, None))

    def test_setor(self):
        itens = {"A": ("Bancos", 5, 1), "B": ("Bancos", 10, 2), "C": ("Bancos", 20, 4), "D": ("Bancos", 8, 3),
                 "E": ("Varejo", -3, 1), "F": ("Varejo", 12, 1.5)}
        r = notas.notas_setor(itens)
        self.assertEqual(r["A"], 100.0)               # mais barata do setor nos dois indicadores
        self.assertEqual(r["C"], 0.0)
        self.assertEqual(r["E"], 0.0)                 # prejuizo
        self.assertIn("F", r)                         # setor com menos de 4 empresas: compara com todas

    def test_tendencia(self):
        hoje = date(2026, 10, 2)
        subindo = [((hoje - timedelta(days=400 - i)).isoformat(), 10 + i * 0.05) for i in range(400)]
        caindo = [(d, 40 - i * 0.05) for i, (d, _) in enumerate(subindo)]
        a, b = notas.indicadores_tendencia(subindo, hoje), notas.indicadores_tendencia(caindo, hoje)
        self.assertGreater(a["mom"], 0)
        self.assertGreater(a["acima200"], 0)
        self.assertLess(b["acima200"], 0)
        r = notas.notas_tendencia({"A": a, "B": b})
        self.assertGreater(r["A"], r["B"])
        self.assertEqual(notas.indicadores_tendencia(subindo[:100], hoje), {})


class TestBacktest(unittest.TestCase):
    def test_datas(self):
        self.assertEqual(backtest.datas_teste("2024-01-02", "2026-10-02"),
                         ["2025-05-01", "2025-11-01", "2026-05-01"])
        self.assertEqual(backtest.datas_teste("2026-01-02", "2026-10-02"), [])

    def test_retorno_com_desdobramento_e_dividendo(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento) VALUES ('X', ?, ?)",
                         [("2025-05-01", 40.0), ("2026-04-30", 22.0)])
        # desdobramento de 1 para 2 no meio: 40 antes equivale a 20 hoje; dividendo de 1 na base de hoje
        r = backtest.retorno_acao(conn, "X", "2025-05-01", "2026-05-01", [("2025-09-01", 2.0)], [("2025-12-01", 1.0)])
        self.assertAlmostEqual(r, (22 + 1) / 20 - 1)
        self.assertIsNone(backtest.retorno_acao(conn, "Y", "2025-05-01", "2026-05-01", [], []))

    def test_cdi(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.executemany("INSERT INTO macro VALUES ('cdi_anual', ?, 10.0)", [(f"2025-05-{d:02d}",) for d in range(1, 29)])
        r = backtest.retorno_cdi(conn, "2025-05-01", "2025-05-28")
        self.assertAlmostEqual(r, 1.1 ** (27 / 252) - 1)
        self.assertIsNone(backtest.retorno_cdi(conn, "2024-01-01", "2024-12-31"))   # sem taxa no periodo

    def test_sugerir_pesos(self):
        padrao = {"a": 50, "b": 50}
        # so "a" ganhou da media: metade do peso segue o padrao, metade vai para quem ganhou
        self.assertEqual(backtest.sugerir_pesos({"a": 0.10, "b": -0.05}, padrao), {"a": 75, "b": 25})
        self.assertEqual(backtest.sugerir_pesos({"a": -0.1, "b": None}, padrao), {"a": 50, "b": 50})

    def test_encadear(self):
        self.assertAlmostEqual(backtest.encadear([0.1, 0.2]), 0.32)
        self.assertIsNone(backtest.encadear([0.1, None]))

    def test_nota_no_passado_so_ve_o_que_existia(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        t, cnpj = _empresa(conn, 1, 20.0)
        # balanco de 2026-06-30 com patrimonio negativo: em 2026-08-01 ainda nao tinha 90 dias, em 2026-10-02 sim
        conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'ITR', 'BPP', 1, '2026-06-30', '', '2026-06-30', 1, '2.03', 'Patrimônio Líquido', -5e8)",
                     (cnpj,))
        antes = notas.calcular_acoes(conn, ate="2026-08-01")[0]
        depois = notas.calcular_acoes(conn)[0]
        self.assertEqual(antes["data"], "2026-07-31")
        self.assertNotIn("Patrimônio líquido negativo", antes["al"])
        self.assertIn("Patrimônio líquido negativo", depois["al"])

    def test_rodar(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        # A sobe 50% por ano e e a mais barata (lucro maior); B e C ficam paradas
        _empresa(conn, 1, lambda d: 10.0 * 1.5 ** ((date.fromisoformat(d) - date(2024, 1, 5)).days / 365), lucro=400e6)
        _empresa(conn, 2, 20.0, lucro=50e6)
        _empresa(conn, 3, 20.0, lucro=60e6)
        uteis = [d for d in _dias("2024-01-01", "2026-10-02", 1) if date.fromisoformat(d).weekday() < 5]
        conn.executemany("INSERT INTO macro VALUES ('cdi_anual', ?, 10.0)", [(d,) for d in uteis])
        with mock.patch.object(backtest, "TOPO", 1):
            r = backtest.rodar(conn)
        self.assertEqual([p["inicio"] for p in r["periodos"]], ["2025-05-01", "2025-11-01", "2026-05-01"])
        p = r["periodos"][0]
        self.assertTrue(p["completo"])
        self.assertEqual(p["acoesTop"][0][0], "EMPB3")
        self.assertAlmostEqual(p["top"], 0.5, delta=0.03)
        self.assertAlmostEqual(p["universo"], 0.5 / 3, delta=0.02)
        self.assertAlmostEqual(p["cdi"], 0.10, delta=0.01)
        self.assertFalse(r["periodos"][-1]["completo"])
        self.assertEqual(r["venceuUniverso"], [1, 1])        # so o periodo de maio de 2025 ja fechou 12 meses
        self.assertEqual(r["anosAcumulados"], 1)
        self.assertIsNone(r["acumuladoLongo"])     # balancos de menos de 5 anos: sem nota de longo prazo
        self.assertEqual(len(r["historico"]["EMPB3"]), 3)
        self.assertIn("graham", r["porMetodo"])
        self.assertEqual(sum(r["pesosSugeridos"].values()), 100)
        self.assertIn("calibrado", p)
        self.assertIsNone(p["ibov"])                  # sem BOVA11 no banco de teste


class TestFundosETesouro(unittest.TestCase):
    def test_nota_fii(self):
        nota, partes = notas.nota_fii(0.09, 1.0, 12, 1_000_000)
        self.assertEqual(partes, {"dividendos": 50.0, "desconto": 50.0, "regularidade": 100.0, "liquidez": 50.0})
        self.assertAlmostEqual(nota, (50 * 35 + 50 * 25 + 100 * 25 + 50 * 15) / 100)
        self.assertEqual(notas.nota_fii(0.13, 0.7, 12, 5e6)[1]["desconto"], 100.0)
        self.assertIsNone(notas.nota_fii(None, 1.0, 12, 1e6)[0])
        self.assertIsNone(notas.nota_fii(0.10, None, 12, 1e6)[0])     # sem valor patrimonial, sem nota
        self.assertEqual(notas.nota_fii(0.20, 0.45, 12, 5e6)[0], 60.0)   # sinais de risco limitam a nota

    def _fundo(self, conn):
        conn.execute("INSERT INTO ativos (ticker, tipo, nome_pregao, isin) VALUES ('FUND11', 'fii', 'FII FUNDO', 'BRFUNDCTF000')")
        conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES ('FUND11', ?, 100.0, 2000000)",
                         [(d,) for d in _dias("2025-09-01", "2026-10-02")] + [("2026-10-02",)])
        conn.execute("""INSERT INTO fii_mensal (cnpj, data_ref, nome, isin, segmento, cotistas, pl, cotas, vp_cota)
                        VALUES ('1', '2026-08-01', 'FUNDO IMOBILIARIO', 'BRFUNDCTF000', 'Logística', 1000, 1e9, 1e7, 110.0)""")
        conn.executemany("INSERT INTO dividendos VALUES ('FUND11', ?, 0.8, 'yahoo')", [(f"2026-{m:02d}-10",) for m in range(1, 10)])
        conn.execute("INSERT INTO dividendos_controle VALUES ('FUND11', '2026-10-01', 'ok-2')")

    def test_calcular_fiis(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        self._fundo(conn)
        f = notas.calcular_fiis(conn)[0]
        self.assertEqual((f["t"], f["seg"], f["meses"]), ("FUND11", "Logística", 9))
        self.assertAlmostEqual(f["dy"], 0.072)
        self.assertAlmostEqual(f["pvp"], 100 / 110)
        self.assertIn("Pagou em só 9 dos últimos 12 meses", f["al"])
        self.assertIsNotNone(f["nota"])

    def test_exportar_monta_tudo(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        _empresa(conn, 1, 20.0)
        self._fundo(conn)
        conn.executemany("INSERT INTO tesouro VALUES ('Tesouro IPCA+', '2035-08-15', ?, ?, ?, 2000, 1990)",
                         [("2025-10-01", 6.9, 7.0), ("2026-10-01", 7.4, 7.5)])
        dados = exportar.montar(conn, {})
        self.assertEqual(len(dados["acoes"]), 1)
        a = dados["acoes"][0]
        self.assertTrue(a["precos5"])
        self.assertEqual(a["hist"][-1][0], "2026-10-02")
        self.assertEqual(dados["fiis"][0]["t"], "FUND11")
        t = dados["tesouro"]["titulos"][0]
        self.assertEqual((t["taxaVenda"], t["taxaAnoAntes"], t["aVenda"]), (7.5, 7.0, True))
        self.assertIn("periodos", dados["backtest"])
        self.assertIn("calendario", dados)
        self.assertIn("fatos", dados)
        self.assertEqual(len(dados["cdiMensal"]), 0)


if __name__ == "__main__":
    unittest.main()


class TestInternacional(unittest.TestCase):
    def _conn(self):
        conn = db.conectar(":memory:")
        dias = _dias("2023-01-02", "2026-10-02")
        for t, preco, vol in (("IVVB11", lambda i: 200 * 1.01 ** i, 5e6), ("XINA11", lambda i: 10.0, 1e5), ("NASD11", lambda i: 10.0, 5e6)):
            conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES (?, ?, ?, ?)",
                             [(t, d, preco(i), vol) for i, d in enumerate(dias) if t != "NASD11" or d < "2026-06-01"])
        return conn

    def test_filtra_e_calcula(self):
        cfg = {"volume_medio_minimo": 1e6, "fundos": [{"t": "IVVB11", "n": "S&P 500", "nucleo": True}, {"t": "XINA11"}, {"t": "NASD11"}, {"t": "NADA11"}]}
        saida = exportar.internacional(self._conn(), cfg)
        self.assertEqual([f["t"] for f in saida], ["IVVB11"])          # XINA11 pouco liquido, NASD11 sem cotacao recente
        f = saida[0]
        self.assertTrue(f["nucleo"])
        self.assertGreater(f["var12"], 0.5)                               # sobe 1% por semana
        self.assertGreater(f["retAno"], 0.5)
        self.assertIsNotNone(f["volat"])
        self.assertLessEqual(len(f["serie"]), 13)

    def test_salto_de_preco_sem_desdobramento(self):
        conn = db.conectar(":memory:")
        conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES ('IVVB11', ?, ?, 5e6)",
                         [(d, 300.0 if d < "2025-06-01" else 30.0) for d in _dias("2023-01-02", "2026-10-02")])
        f = exportar.internacional(conn, {"fundos": [{"t": "IVVB11"}]})[0]
        self.assertIsNone(f["var12"]); self.assertIsNone(f["retAno"])

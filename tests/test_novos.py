"""Backtest, fundos imobiliarios, Tesouro, Ebitda e conferencia de dividendos."""
import unittest
from datetime import date, timedelta
from unittest import mock

from radar import backtest, db, exportar, notas


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
        self.assertEqual(len(r["historico"]["EMPB3"]), 3)


class TestFundosETesouro(unittest.TestCase):
    def test_nota_fii(self):
        nota, partes = notas.nota_fii(0.09, 1.0, 12, 1_000_000)
        self.assertEqual(partes, {"dividendos": 50.0, "desconto": 50.0, "regularidade": 100.0, "liquidez": 50.0})
        self.assertAlmostEqual(nota, (50 * 35 + 50 * 25 + 100 * 25 + 50 * 15) / 100)
        self.assertEqual(notas.nota_fii(0.13, 0.7, 12, 5e6)[1]["desconto"], 100.0)
        self.assertIsNone(notas.nota_fii(None, 1.0, 12, 1e6)[0])

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


if __name__ == "__main__":
    unittest.main()

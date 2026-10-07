import math
import unittest

from radar import db, notas

CNPJ = "11111111000111"


class TestPeriodos(unittest.TestCase):
    FLUXO = {
        ("2025-01-01", "2025-03-31"): 20, ("2025-01-01", "2025-06-30"): 45, ("2025-04-01", "2025-06-30"): 25,
        ("2025-01-01", "2025-09-30"): 75, ("2025-07-01", "2025-09-30"): 30, ("2025-01-01", "2025-12-31"): 100,
        ("2026-01-01", "2026-03-31"): 28, ("2026-01-01", "2026-06-30"): 60, ("2026-04-01", "2026-06-30"): 32,
    }

    def test_quarto_trimestre_e_o_ano_menos_nove_meses(self):
        q = notas.trimestres(self.FLUXO)
        self.assertEqual(q["2025-12-31"], 25)      # 100 - 75
        self.assertEqual(q["2025-06-30"], 25)
        self.assertEqual(len(q), 6)

    def test_ultimos_12_meses_soma_quatro_trimestres(self):
        # 30 (3T25) + 25 (4T25) + 28 (1T26) + 32 (2T26) = 115
        self.assertEqual(notas.ultimos_12_meses(self.FLUXO), (115, "2026-06-30", "4 trimestres"))

    def test_sem_trimestres_usa_o_ultimo_ano(self):
        fluxo = {("2024-01-01", "2024-12-31"): 80, ("2025-01-01", "2025-12-31"): 100}
        self.assertEqual(notas.ultimos_12_meses(fluxo), (100, "2025-12-31", "ultimo ano"))

    def test_trimestre_faltando_usa_o_ultimo_ano(self):
        fluxo = dict(self.FLUXO)
        del fluxo[("2025-07-01", "2025-09-30")], fluxo[("2025-01-01", "2025-09-30")]
        self.assertEqual(notas.ultimos_12_meses(fluxo)[2], "ultimo ano")

    def test_sem_dado(self):
        self.assertEqual(notas.ultimos_12_meses({}), (None, None, None))


class TestNotas(unittest.TestCase):
    def test_graham_bate_com_a_formula_por_acao(self):
        # preco 20, LPA 2, VPA 10: valor = raiz(22,5 x 2 x 10) = 21,2132; P/L 10 e P/VP 2
        nota, valor, margem = notas.nota_graham(20, 10, 2)
        self.assertAlmostEqual(valor, math.sqrt(22.5 * 2 * 10), places=6)
        self.assertAlmostEqual(margem, (21.2132 - 20) / 21.2132, places=4)
        self.assertAlmostEqual(nota, 55.72, places=1)

    def test_graham_prejuizo_e_sem_dado(self):
        self.assertEqual(notas.nota_graham(20, -5, 2)[0], 0.0)
        self.assertEqual(notas.nota_graham(20, None, 2), (None, None, None))

    def test_barsi(self):
        # dividendo medio 1,20 => teto 20,00; preco 15 => margem 25% => 75; setor BESST +10
        nota, teto, margem = notas.nota_barsi(15, 1.2, 5, True)
        self.assertAlmostEqual(teto, 20.0)
        self.assertAlmostEqual(margem, 0.25)
        self.assertAlmostEqual(nota, 85.0)
        self.assertAlmostEqual(notas.nota_barsi(15, 1.2, 3, False)[0], 55.0)   # pagou em 3 de 5 anos: -20
        self.assertEqual(notas.nota_barsi(15, 0, 0, False), (0.0, None, None))
        self.assertEqual(notas.nota_barsi(15, None, 0, False), (None, None, None))
        self.assertEqual(notas.nota_barsi(100, 1.2, 5, False)[0], 0.0)         # muito acima do teto

    def test_bazin(self):
        self.assertAlmostEqual(notas.nota_bazin(0.08, 5, 0.2, False), 80.0)
        self.assertAlmostEqual(notas.nota_bazin(0.03, 5, 0.2, False), 30.0)
        self.assertAlmostEqual(notas.nota_bazin(0.12, 5, None, True), 100.0)
        self.assertAlmostEqual(notas.nota_bazin(0.08, 4, 0.2, False), 56.0)    # nao pagou em todos os anos
        self.assertAlmostEqual(notas.nota_bazin(0.08, 5, 1.5, False), 65.0)    # divida maior que o patrimonio
        self.assertIsNone(notas.nota_bazin(None, 0, None, False))

    def test_checklist(self):
        itens = [("a", True), ("b", True), ("c", False), ("d", True), ("e", None), ("f", True), ("g", False)]
        self.assertAlmostEqual(notas.nota_checklist(itens), 100 * 4 / 6)
        self.assertIsNone(notas.nota_checklist(itens[:4]))

    def test_greenblatt(self):
        r = notas.notas_greenblatt({"A": (0.20, 0.30), "B": (0.10, 0.20), "C": (0.05, 0.10), "D": (-0.10, -0.20)})
        self.assertEqual(r, {"A": 100.0, "B": 55.0, "C": 10.0, "D": 0.0})

    def test_nota_final_ignora_metodo_sem_dado(self):
        pesos = {"barsi": 20, "bazin": 15, "qualidade": 30, "graham": 15, "greenblatt": 20}
        m = {"barsi": 80, "bazin": 60, "qualidade": 70, "graham": 50, "greenblatt": None}
        self.assertAlmostEqual(notas.nota_final(m, pesos), (80 * 20 + 60 * 15 + 70 * 30 + 50 * 15) / 80)
        self.assertIsNone(notas.nota_final({}, pesos))

    def test_longo_prazo(self):
        # tudo atendido menos dividendos (15) e setor perene (5); trimestres sem dado (10): 75 de 95
        c = notas.criterios_longo_prazo(True, None, True, True, True, True, 3, 12, "Varejo")
        self.assertEqual(sum(peso for _, _, peso in c), 105)
        self.assertAlmostEqual(notas.nota_longo_prazo(c), 100 * 75 / 95)
        self.assertEqual(notas.nota_longo_prazo(c, prejuizo=True), 30.0)
        # P/L negativo ou acima de 15 nao passa; setor perene passa
        c = dict((txt, ok) for txt, ok, _ in notas.criterios_longo_prazo(True, True, True, True, None, None, 5, -4, "Saneamento"))
        self.assertFalse(c["Preço sobre o lucro entre 0 e 15"])
        self.assertTrue(c["Setor perene (bancos, seguros, energia, saneamento ou telecom)"])
        # menos de 60% do peso com dado
        self.assertIsNone(notas.nota_longo_prazo(notas.criterios_longo_prazo(None, None, None, True, None, None, None, None, "Varejo")))

    def test_cripto(self):
        # 20% acima da media => 80; volatilidade 40% => 60; 1a posicao => 100
        self.assertAlmostEqual(notas.nota_cripto(20, 40, 1), 0.45 * 80 + 0.30 * 60 + 0.25 * 100)
        self.assertIsNone(notas.nota_cripto(None, 40, 1))

    def test_setor(self):
        self.assertEqual(notas.classificar_setor("Bancos"), ("Bancos", True, True))
        self.assertEqual(notas.classificar_setor("Emp. Adm. Part. - Energia Elétrica")[1:], (True, False))
        self.assertEqual(notas.classificar_setor("Seguradoras e Corretoras")[0], "Seguros")
        self.assertEqual(notas.classificar_setor("Comércio (Atacado e Varejo)"), ("Comércio", False, False))
        self.assertEqual(notas.classificar_setor("Emp. Adm. Part. - Sem Setor Principal")[0], "Sem setor")
        self.assertEqual(notas.classificar_setor("Construção Civil, Mat. Constr. e Decoração")[0], "Construção Civil")


class TestFundamentos(unittest.TestCase):
    def test_plano_comum(self):
        linhas = [
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.01", "Receita de Venda de Bens e/ou Serviços", 1000.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", 300.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.09", "Resultado Líquido das Operações Continuadas", 210.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.11", "Lucro/Prejuízo Consolidado do Período", 210.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.11.01", "Atribuído a Sócios da Empresa Controladora", 200.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.11.02", "Atribuído a Sócios Não Controladores", 10.0),
            ("BPP", "2025-12-31", "", "2025-12-31", "2.01.04", "Empréstimos e Financiamentos", 100.0),
            ("BPP", "2025-12-31", "", "2025-12-31", "2.02.01", "Empréstimos e Financiamentos", 400.0),
            ("BPP", "2025-12-31", "", "2025-12-31", "2.03", "Patrimônio Líquido Consolidado", 1050.0),
            ("BPP", "2025-12-31", "", "2025-12-31", "2.03.09", "Participação dos Acionistas Não Controladores", 50.0),
            ("BPA", "2025-12-31", "", "2025-12-31", "1.01.01", "Caixa e Equivalentes de Caixa", 120.0),
            ("BPA", "2025-12-31", "", "2025-12-31", "1.01.02", "Aplicações Financeiras", 30.0),
        ]
        f = notas.montar_fundamentos(linhas)
        periodo = ("2025-01-01", "2025-12-31")
        self.assertEqual(f["lucro"][periodo], 200.0)        # parte dos controladores
        self.assertEqual(f["receita"][periodo], 1000.0)
        self.assertEqual(f["ebit"][periodo], 300.0)
        self.assertEqual((f["pl_total"], f["pl"]), (1050.0, 1000.0))
        self.assertEqual((f["divida"], f["caixa"]), (500.0, 150.0))
        self.assertFalse(f["plano_financeiro"])

    def test_plano_de_banco(self):
        linhas = [
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.01", "Receitas da Intermediação Financeira", 5000.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.05", "Resultado Antes dos Tributos sobre o Lucro", 900.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.09", "Lucro ou Prejuízo Líquido Consolidado do Período", 700.0),
            ("BPP", "2025-12-31", "", "2025-12-31", "2.08", "Patrimônio Líquido Consolidado", 6000.0),
        ]
        f = notas.montar_fundamentos(linhas)
        self.assertEqual(f["lucro"][("2025-01-01", "2025-12-31")], 700.0)
        self.assertEqual(f["pl"], 6000.0)
        self.assertTrue(f["plano_financeiro"])
        self.assertIsNone(f.get("divida"))


class TestCasosReais(unittest.TestCase):
    """Situacoes encontradas na primeira coleta real."""

    def test_linha_da_controladora_zerada_usa_o_lucro_total(self):
        linhas = [
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.11", "Lucro/Prejuízo Consolidado do Período", 90.0),
            ("DRE", "2025-12-31", "2025-01-01", "2025-12-31", "3.11.01", "Atribuído a Sócios da Empresa Controladora", 0.0),
        ]
        self.assertEqual(notas.montar_fundamentos(linhas)["lucro"][("2025-01-01", "2025-12-31")], 90.0)

    def test_duas_classes_de_acao(self):
        # 100 mi de ON a R$ 30 e 200 mi de PN a R$ 20; lucro de 600 mi e patrimonio de 3 bi
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        cnpj = "22222222000122"
        conn.execute("INSERT INTO empresas (cnpj, nome, setor) VALUES (?, 'DUAS CLASSES S.A.', 'Bancos')", (cnpj,))
        for ticker, preco, volume in (("DUAS3", 30.0, 1e6), ("DUAS4", 20.0, 9e6)):
            conn.execute("INSERT INTO ativos (ticker, tipo, nome_pregao, ultima_data, cnpj) VALUES (?, 'acao', 'DUAS', '2026-10-02', ?)", (ticker, cnpj))
            conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES (?, ?, ?, ?)",
                             [(ticker, f"2026-09-{d:02d}", preco, volume) for d in range(1, 29)] + [(ticker, "2026-10-02", preco, volume)])
        conn.execute("INSERT INTO capital (cnpj, dt_refer, on_total, pn_total, total, on_tes, pn_tes, tes) VALUES (?, '2025-12-31', 100e6, 200e6, 300e6, 0, 0, 0)", (cnpj,))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '2', 'DFP', 'DRE', 1, '2025-12-31', '2025-01-01', '2025-12-31', 1, '3.09', 'Lucro ou Prejuízo Líquido Consolidado do Período', 600e6)", (cnpj,))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '2', 'DFP', 'BPP', 1, '2025-12-31', '', '2025-12-31', 1, '2.08', 'Patrimônio Líquido Consolidado', 3e9)", (cnpj,))
        r = notas.calcular_acoes(conn)[0]
        self.assertEqual(r["t"], "DUAS4")                               # o papel mais negociado representa a empresa
        self.assertAlmostEqual(r["valorMercado"], 100e6 * 30 + 200e6 * 20)
        self.assertAlmostEqual(r["pl"], 20 * 300e6 / 600e6)             # preco do papel x todas as acoes / lucro = 10
        self.assertAlmostEqual(r["pvp"], 2.0)
        self.assertIsNone(r["m"]["greenblatt"])                         # banco
        self.assertIsNone(r["m"]["barsi"])                              # dividendos nao consultados


class TestAjustes(unittest.TestCase):
    def _empresa(self, conn, lucros, desdobro=None):
        cnpj = "33333333000133"
        conn.execute("INSERT INTO empresas (cnpj, nome, setor) VALUES (?, 'AJUSTE S.A.', 'Comércio')", (cnpj,))
        conn.execute("INSERT INTO ativos (ticker, tipo, nome_pregao, ultima_data, cnpj) VALUES ('AJUS3', 'acao', 'AJUSTE', '2026-10-02', ?)", (cnpj,))
        # ate julho de 2026 o papel valia 40; depois do desdobramento de 1 para 2, vale 20
        dias = [(f"2025-{m:02d}-15", 40.0) for m in (9, 10, 11, 12)] + [(f"2026-{m:02d}-15", 40.0) for m in range(1, 8)]
        dias += [(f"2026-09-{d:02d}", 20.0) for d in range(1, 29)] + [("2026-10-02", 20.0)]
        conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES ('AJUS3', ?, ?, 5000000)", dias)
        conn.execute("INSERT INTO capital (cnpj, dt_refer, on_total, pn_total, total, on_tes, pn_tes, tes) VALUES (?, '2026-06-30', 50e6, 0, 50e6, 0, 0, 0)", (cnpj,))
        for ano, lucro in lucros.items():
            for cd, ds, v in (("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", lucro * 1.5), ("3.11", "Lucro/Prejuízo do Período", lucro)):
                conn.execute("INSERT INTO demonstrativos VALUES (?, '3', 'DFP', 'DRE', 1, ?, ?, ?, 1, ?, ?, ?)",
                             (cnpj, f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", cd, ds, v))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '3', 'DFP', 'BPP', 1, '2025-12-31', '', '2025-12-31', 1, '2.03', 'Patrimônio Líquido', 1e9)", (cnpj,))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '3', 'DFP', 'BPP', 1, '2025-12-31', '', '2025-12-31', 1, '2.01.04', 'Empréstimos e Financiamentos', 0)", (cnpj,))
        if desdobro:
            conn.execute("INSERT INTO desdobramentos VALUES ('AJUS3', ?, ?, 'yahoo')", desdobro)

    def test_desdobramento_ajusta_acoes_e_precos_antigos(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        self._empresa(conn, {2023: 100e6, 2024: 100e6, 2025: 100e6}, desdobro=("2026-08-01", 2.0))
        r = notas.calcular_acoes(conn)[0]
        self.assertAlmostEqual(r["valorMercado"], 100e6 * 20)      # 50 mi de acoes viraram 100 mi
        self.assertAlmostEqual(r["pl"], 20.0)
        self.assertAlmostEqual(r["var12"], 0.0)                    # 40 antes do desdobramento equivale a 20 hoje
        self.assertFalse(any("Caiu" in a for a in r["al"]))

    def test_sem_ajuste_a_queda_seria_falsa(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        self._empresa(conn, {2023: 100e6, 2024: 100e6, 2025: 100e6})
        r = notas.calcular_acoes(conn)[0]
        self.assertAlmostEqual(r["var12"], -0.5)
        self.assertAlmostEqual(r["valorMercado"], 50e6 * 20)

    def test_lucro_fora_da_curva_usa_a_media_de_tres_anos(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        self._empresa(conn, {2022: 50e6, 2023: 50e6, 2024: 50e6, 2025: 500e6})   # ultimo ano: 10 vezes o normal
        r = notas.calcular_acoes(conn)[0]
        self.assertAlmostEqual(r["pl"], 50e6 * 20 / 500e6)         # o P/L mostrado continua sendo o de 12 meses: 2
        self.assertTrue(r["lucroNormalizado"])
        # Graham usa a media de 2023 a 2025 (200 mi): P/L 5 e P/VP 1 => valor = 20 x raiz(22,5 / 5)
        self.assertAlmostEqual(r["graham"], 20 * math.sqrt(22.5 / 5), places=4)
        self.assertTrue(any("média" in a for a in r["al"]))
        self.assertTrue(r["distorcao"])


class TestCalculoCompleto(unittest.TestCase):
    def test_uma_empresa_do_banco_ao_ranking(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO empresas (cnpj, cd_cvm, nome, setor) VALUES (?, '1', 'EMPRESA TESTE S.A.', 'Energia Elétrica')", (CNPJ,))
        conn.execute("INSERT INTO ativos (ticker, tipo, nome_pregao, ultima_data, cnpj) VALUES ('TEST3', 'acao', 'TESTE', '2026-10-02', ?)", (CNPJ,))
        dias = [f"2026-{m:02d}-{d:02d}" for m in (8, 9) for d in range(1, 29)] + ["2026-10-01", "2026-10-02"]
        conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES ('TEST3', ?, 20.0, 5000000)", [(d,) for d in dias])
        conn.execute("INSERT INTO capital (cnpj, dt_refer, on_total, pn_total, total, on_tes, pn_tes, tes) VALUES (?, '2025-12-31', 100e6, 0, 100e6, 0, 0, 0)", (CNPJ,))
        for ano in range(2021, 2026):
            lucro = 200e6 if ano == 2025 else 100e6 + (ano - 2021) * 10e6
            for cd, ds, v in (("3.01", "Receita", 1e9 * (1.1 ** (ano - 2021))), ("3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", 300e6),
                              ("3.11", "Lucro/Prejuízo do Período", lucro)):
                conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'DRE', 1, ?, ?, ?, 1, ?, ?, ?)",
                             (CNPJ, f"{ano}-12-31", f"{ano}-01-01", f"{ano}-12-31", cd, ds, v))
        for cd, ds, v in (("2.03", "Patrimônio Líquido", 1e9), ("2.01.04", "Empréstimos e Financiamentos", 100e6), ("2.02.01", "Empréstimos e Financiamentos", 300e6)):
            conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'BPP', 1, '2025-12-31', '', '2025-12-31', 1, ?, ?, ?)", (CNPJ, cd, ds, v))
        conn.execute("INSERT INTO demonstrativos VALUES (?, '1', 'DFP', 'BPA', 1, '2025-12-31', '', '2025-12-31', 1, '1.01.01', 'Caixa e Equivalentes de Caixa', 100e6)", (CNPJ,))
        conn.executemany("INSERT INTO dividendos VALUES ('TEST3', ?, 1.2, 'yahoo')", [(f"{a}-05-10",) for a in range(2022, 2027)])
        conn.execute("INSERT INTO dividendos_controle VALUES ('TEST3', '2026-10-02', 'ok')")

        lista = notas.calcular_acoes(conn)
        self.assertEqual(len(lista), 1)
        r = lista[0]
        self.assertEqual((r["t"], r["s"], r["p"]), ("TEST3", "Energia elétrica", 20.0))
        self.assertAlmostEqual(r["valorMercado"], 2e9)                 # 100 milhoes de acoes x R$ 20
        self.assertAlmostEqual(r["pl"], 10.0)                          # 2 bi / 200 mi de lucro
        self.assertAlmostEqual(r["pvp"], 2.0)                          # 2 bi / 1 bi de patrimonio
        self.assertAlmostEqual(r["roe"], 0.20)
        self.assertAlmostEqual(r["graham"], math.sqrt(22.5 * 2 * 10), places=4)
        self.assertAlmostEqual(r["dy"], 0.06)                          # 1,20 / 20
        self.assertAlmostEqual(r["teto"], 20.0)                        # 1,20 / 6%
        self.assertAlmostEqual(r["divEbit"], 1.0)                      # (400 - 100) / 300
        self.assertEqual(r["m"]["barsi"], 60)                          # margem zero = 50, setor BESST +10
        self.assertEqual(r["m"]["bazin"], 60)
        self.assertEqual(r["m"]["greenblatt"], 100)                    # unica empresa do ranking
        self.assertEqual(r["m"]["graham"], 56)
        ok = dict((txt, v) for txt, v in r["check"])
        self.assertTrue(ok["Lucro em cada um dos últimos 5 anos"])
        self.assertTrue(ok["Receita cresceu 5% ao ano ou mais em 5 anos"])
        self.assertIsNone(ok["Lucro em todos os trimestres dos últimos 5 anos"])
        self.assertEqual(r["al"], ["Último balanço tem mais de 9 meses"])
        self.assertEqual(r["conf"], [10, 10])
        self.assertFalse(r["distorcao"])
        self.assertEqual(r["aplicaveis"], 5)
        lp = dict(r["lpCheck"])
        self.assertTrue(lp["Pagou dividendos em todos os últimos 5 anos"])
        self.assertTrue(lp["Preço sobre o lucro entre 0 e 15"])           # P/L 10
        self.assertIsNone(lp["Lucro em todos os trimestres dos últimos 5 anos"])
        self.assertEqual(r["lp"], 100)                                   # todos os criterios com dado atendidos


if __name__ == "__main__":
    unittest.main()

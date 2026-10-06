import tempfile
import unittest
from datetime import date
from pathlib import Path

from radar import db
from radar.fontes import cvm
from tests import fabrica

PETRO = "33.000.167/0001-01"
SANEPAR = "76.484.013/0001-45"
HOJE = date(2026, 10, 6)


class TestNumero(unittest.TestCase):
    def test_formatos(self):
        self.assertEqual(cvm.numero("1234567.0000000000"), 1234567.0)
        self.assertEqual(cvm.numero("1.234,56"), 1234.56)
        self.assertEqual(cvm.numero("-10,5"), -10.5)
        self.assertIsNone(cvm.numero(""))
        self.assertIsNone(cvm.numero("abc"))


class TestCadastro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.conectar(":memory:")
        self.addCleanup(self.conn.close)

    def tearDown(self):
        self.tmp.cleanup()

    def _fca(self, ano, geral, valores):
        return fabrica.zip_fca(Path(self.tmp.name) / f"fca_{ano}.zip", ano, geral, valores)

    def test_grava_empresa_e_codigos(self):
        zp = self._fca(2026,
                       [[PETRO, "2026-05-30", 1, 1, "PETROLEO BRASILEIRO S.A.", "9512", "Ativo", "Petróleo e Gás"]],
                       [[PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações Ordinárias", "PETR3", "Bolsa", "", "", "Nível 2"],
                        [PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações Preferenciais", "PETR4", "Bolsa", "", "", "Nível 2"],
                        [PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Debêntures", "", "Balcão", "", "", ""]])
        emp, tic = cvm.carregar_fca(self.conn, zp, HOJE)
        self.assertEqual((emp, tic), (1, 2))
        linha = self.conn.execute("SELECT cnpj, cd_cvm, setor FROM empresas").fetchone()
        self.assertEqual(linha, ("33000167000101", "9512", "Petróleo e Gás"))
        self.assertEqual(sorted(t for (t,) in self.conn.execute("SELECT ticker FROM empresa_tickers")),
                         ["PETR3", "PETR4"])

    def test_formulario_mais_novo_substitui_o_antigo(self):
        antigo = self._fca(2025, [[PETRO, "2025-05-30", 1, 1, "PETROBRAS", "9512", "Ativo", "Petróleo"]],
                           [[PETRO, "2025-05-30", 1, 1, "PETROBRAS", "Ações", "PETR3", "Bolsa", "", "", ""],
                            [PETRO, "2025-05-30", 1, 1, "PETROBRAS", "Ações", "PETR5", "Bolsa", "", "", ""]])
        novo = self._fca(2026, [[PETRO, "2026-05-30", 2, 1, "PETROBRAS", "9512", "Ativo", "Petróleo e Gás"]],
                         [[PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações", "XXXX3", "Bolsa", "", "", ""],
                          [PETRO, "2026-05-30", 2, 1, "PETROBRAS", "Ações", "PETR3", "Bolsa", "", "", ""],
                          [PETRO, "2026-05-30", 2, 1, "PETROBRAS", "Ações", "PETR4", "Bolsa", "", "", ""]])
        cvm.carregar_fca(self.conn, antigo, HOJE)
        cvm.carregar_fca(self.conn, novo, HOJE)
        self.assertEqual(sorted(t for (t,) in self.conn.execute("SELECT ticker FROM empresa_tickers")),
                         ["PETR3", "PETR4"])
        self.assertEqual(self.conn.execute("SELECT setor FROM empresas").fetchone()[0], "Petróleo e Gás")

    def test_codigo_com_negociacao_encerrada_fica_fora(self):
        zp = self._fca(2026, [[PETRO, "2026-05-30", 1, 1, "PETROBRAS", "9512", "Ativo", "Petróleo"]],
                       [[PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações", "PETR4", "Bolsa", "2000-01-01", "", ""],
                        [PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações", "PETR6", "Bolsa", "2000-01-01", "2020-01-01", ""]])
        cvm.carregar_fca(self.conn, zp, HOJE)
        self.assertEqual([t for (t,) in self.conn.execute("SELECT ticker FROM empresa_tickers")], ["PETR4"])

    def test_coluna_ausente_gera_erro_claro(self):
        zp = Path(self.tmp.name) / "ruim.zip"
        import zipfile
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("fca_cia_aberta_valor_mobiliario_2026.csv", "CNPJ_Companhia;Data_Referencia;Outra\r\n")
        with self.assertRaises(cvm.ErroFormato) as ctx:
            cvm.carregar_fca(self.conn, zp, HOJE)
        self.assertIn("CODIGO_NEGOCIACAO", str(ctx.exception))
        self.assertIn("OUTRA", str(ctx.exception))

    def test_vinculo_pelo_codigo_e_pelas_quatro_letras(self):
        zp = self._fca(2026, [[SANEPAR, "2026-05-30", 1, 1, "SANEPAR", "18627", "Ativo", "Saneamento"]],
                       [[SANEPAR, "2026-05-30", 1, 1, "SANEPAR", "Ações", "SAPR3", "Bolsa", "", "", ""],
                        [SANEPAR, "2026-05-30", 1, 1, "SANEPAR", "Ações", "SAPR4", "Bolsa", "", "", ""]])
        cvm.carregar_fca(self.conn, zp, HOJE)
        self.conn.executemany("INSERT INTO ativos (ticker, tipo) VALUES (?, ?)",
                              [("SAPR4", "acao"), ("SAPR11", "unit"), ("HGLG11", "fii"), ("ZZZZ3", "acao")])
        exatos, por_raiz = db.vincular_ativos(self.conn)
        self.assertEqual((exatos, por_raiz), (1, 1))
        v = dict((t, (c, k)) for t, c, k in self.conn.execute("SELECT ticker, cnpj, vinculo FROM ativos"))
        self.assertEqual(v["SAPR4"], ("76484013000145", "cadastro"))
        self.assertEqual(v["SAPR11"], ("76484013000145", "raiz"))
        self.assertEqual(v["HGLG11"], (None, None))
        self.assertEqual(v["ZZZZ3"], (None, None))


class TestDemonstrativos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.conectar(":memory:")
        self.addCleanup(self.conn.close)

    def tearDown(self):
        self.tmp.cleanup()

    def _zip(self, origem, ano, arquivos):
        return fabrica.zip_demonstrativos(Path(self.tmp.name) / f"{origem}_{ano}.zip", origem, ano, arquivos)

    def _valor(self, **filtro):
        cond = " AND ".join(f"{k} = ?" for k in filtro)
        linha = self.conn.execute(f"SELECT valor FROM demonstrativos WHERE {cond}", tuple(filtro.values())).fetchone()
        return linha[0] if linha else None

    def test_escala_exercicio_e_nivel_da_conta(self):
        L = fabrica.linha_conta
        zp = self._zip("dfp", 2025, {
            "DRE_con": [
                L(PETRO, "2025-12-31", "3.01", "Receita", 500000, dt_ini="2025-01-01"),
                L(PETRO, "2025-12-31", "3.01", "Receita", 450000, dt_ini="2024-01-01", ordem="PENÚLTIMO"),
                L(PETRO, "2025-12-31", "3.11", "Lucro", 100000, dt_ini="2025-01-01"),
                L(PETRO, "2025-12-31", "3.99.01.01", "LPA ON", 7.5, dt_ini="2025-01-01", escala="UNIDADE"),
            ],
            "BPA_con": [L(PETRO, "2025-12-31", "1", "Ativo Total", 1000000)],
            "BPP_con": [L(PETRO, "2025-12-31", "2.03", "Patrimônio Líquido", 400000)],
        })
        n = cvm.carregar_demonstrativos(self.conn, zp, "DFP", ["BPA", "BPP", "DRE", "DFC_MI"], 3)
        self.assertEqual(n, 4)  # penultimo exercicio e conta de nivel 4 ficam fora
        self.assertEqual(self._valor(demonstrativo="DRE", cd_conta="3.01"), 500000 * 1000.0)
        self.assertEqual(self._valor(demonstrativo="BPA", cd_conta="1"), 1000000 * 1000.0)
        self.assertEqual(self._valor(demonstrativo="BPP", cd_conta="2.03"), 400000 * 1000.0)
        linha = self.conn.execute("SELECT cnpj, origem, consolidado, dt_ini_exerc FROM demonstrativos "
                                  "WHERE cd_conta='3.11'").fetchone()
        self.assertEqual(linha, ("33000167000101", "DFP", 1, "2025-01-01"))

    def test_usa_consolidado_e_cai_para_individual_quando_nao_ha(self):
        L = fabrica.linha_conta
        zp = self._zip("dfp", 2025, {
            "DRE_con": [L(PETRO, "2025-12-31", "3.11", "Lucro", 100, dt_ini="2025-01-01")],
            "DRE_ind": [L(PETRO, "2025-12-31", "3.11", "Lucro", 90, dt_ini="2025-01-01"),
                        L(SANEPAR, "2025-12-31", "3.11", "Lucro", 50, dt_ini="2025-01-01")],
        })
        cvm.carregar_demonstrativos(self.conn, zp, "DFP", ["DRE"], 3)
        linhas = dict((c, (v, k)) for c, v, k in
                      self.conn.execute("SELECT cnpj, valor, consolidado FROM demonstrativos"))
        self.assertEqual(linhas["33000167000101"], (100000.0, 1))
        self.assertEqual(linhas["76484013000145"], (50000.0, 0))

    def test_reapresentacao_mais_nova_prevalece(self):
        L = fabrica.linha_conta
        v2 = self._zip("dfp", 2025, {"DRE_con": [L(PETRO, "2025-12-31", "3.11", "Lucro", 120, dt_ini="2025-01-01", versao=2)]})
        cvm.carregar_demonstrativos(self.conn, v2, "DFP", ["DRE"], 3)
        v1 = self._zip("dfp", 2024, {"DRE_con": [L(PETRO, "2025-12-31", "3.11", "Lucro", 100, dt_ini="2025-01-01", versao=1)]})
        cvm.carregar_demonstrativos(self.conn, v1, "DFP", ["DRE"], 3)
        self.assertEqual(self._valor(cd_conta="3.11"), 120000.0)

    def test_trimestral_guarda_acumulado_e_trimestre_separados(self):
        L = fabrica.linha_conta
        zp = self._zip("itr", 2026, {"DRE_con": [
            L(PETRO, "2026-06-30", "3.11", "Lucro", 60, dt_ini="2026-01-01"),
            L(PETRO, "2026-06-30", "3.11", "Lucro", 35, dt_ini="2026-04-01"),
        ]})
        cvm.carregar_demonstrativos(self.conn, zp, "ITR", ["DRE"], 3)
        self.assertEqual(self._valor(origem="ITR", dt_ini_exerc="2026-01-01"), 60000.0)
        self.assertEqual(self._valor(origem="ITR", dt_ini_exerc="2026-04-01"), 35000.0)

    def test_filtro_de_empresas_permitidas(self):
        L = fabrica.linha_conta
        zp = self._zip("dfp", 2025, {"DRE_con": [
            L(PETRO, "2025-12-31", "3.11", "Lucro", 1, dt_ini="2025-01-01"),
            L(SANEPAR, "2025-12-31", "3.11", "Lucro", 2, dt_ini="2025-01-01")]})
        n = cvm.carregar_demonstrativos(self.conn, zp, "DFP", ["DRE"], 3, permitidos={"76484013000145"})
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()

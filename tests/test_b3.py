import tempfile
import unittest
from datetime import date
from pathlib import Path

from radar import db
from radar.fontes import b3_cotahist as b3
from tests import fabrica


class TestParse(unittest.TestCase):
    def test_campos_de_uma_linha(self):
        r = b3.parse_linha(fabrica.linha_cotahist())
        self.assertEqual(r["data"], "2026-10-02")
        self.assertEqual(r["ticker"], "PETR4")
        self.assertEqual(r["codbdi"], "02")
        self.assertEqual(r["nome_pregao"], "PETROBRAS")
        self.assertEqual(r["especificacao"], "PN      N2")
        self.assertAlmostEqual(r["abertura"], 38.00)
        self.assertAlmostEqual(r["maxima"], 38.50)
        self.assertAlmostEqual(r["minima"], 37.90)
        self.assertAlmostEqual(r["medio"], 38.20)
        self.assertAlmostEqual(r["fechamento"], 38.10)
        self.assertEqual(r["negocios"], 12345)
        self.assertEqual(r["quantidade"], 1000000)
        self.assertAlmostEqual(r["volume"], 38100000.00)
        self.assertEqual(r["isin"], "BRPETRACNPR6")

    def test_preco_por_lote_e_dividido_pelo_fator(self):
        r = b3.parse_linha(fabrica.linha_cotahist(ult=150000, fator=1000))
        self.assertAlmostEqual(r["fechamento"], 1.50)

    def test_cabecalho_e_rodape_sao_ignorados(self):
        self.assertIsNone(b3.parse_linha("00COTAHIST.2026BOVESPA 20261002".ljust(245)))
        self.assertIsNone(b3.parse_linha("99COTAHIST.2026BOVESPA 20261002".ljust(245)))
        self.assertIsNone(b3.parse_linha("01curta"))

    def test_classificacao(self):
        self.assertEqual(b3.classificar("ON      NM", "02"), "acao")
        self.assertEqual(b3.classificar("PNB     N1", "02"), "acao")
        self.assertEqual(b3.classificar("UNT     N2", "02"), "unit")
        self.assertEqual(b3.classificar("CI", "12"), "fii")
        self.assertEqual(b3.classificar("DRN", "02"), "bdr")
        self.assertEqual(b3.classificar("CI", "14"), "etf")


class TestPlano(unittest.TestCase):
    def test_primeira_coleta_le_todos_os_anos(self):
        self.assertEqual(b3.planejar_arquivos(set(), None, date(2026, 10, 6), 3), [2024, 2025, 2026])

    def test_coleta_seguinte_le_so_o_ano_corrente(self):
        self.assertEqual(b3.planejar_arquivos({2024, 2025, 2026}, date(2026, 10, 5), date(2026, 10, 6), 3), [2026])

    def test_virada_de_ano_completa_o_ano_anterior(self):
        self.assertEqual(b3.planejar_arquivos({2024, 2025}, date(2025, 12, 19), date(2026, 1, 5), 3), [2025, 2026])

    def test_aumentar_o_historico_busca_os_anos_que_faltam(self):
        self.assertEqual(b3.planejar_arquivos({2025, 2026}, date(2026, 10, 5), date(2026, 10, 6), 4), [2023, 2024, 2026])

    def test_nome_do_arquivo(self):
        self.assertEqual(b3.nome_arquivo(2026), "COTAHIST_A2026.ZIP")


class TestLeituraEGravacao(unittest.TestCase):
    def test_filtra_mercado_e_tipo_de_lote_e_grava(self):
        linhas = [
            fabrica.linha_cotahist(data="20261001", ult=3700),
            fabrica.linha_cotahist(data="20261002", ult=3810),
            fabrica.linha_cotahist(ticker="PETR4F", codbdi="96", tpmerc="020"),           # fracionario: fora
            fabrica.linha_cotahist(ticker="PETRJ380", codbdi="78", tpmerc="070"),         # opcao: fora
            fabrica.linha_cotahist(ticker="HGLG11", codbdi="12", especi="CI", nome="FII HGLG", ult=14790,
                                   isin="BRHGLGCTF004"),
            fabrica.linha_cotahist(ticker="SAPR11", especi="UNT     N2", nome="SANEPAR", ult=3521),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            zp = fabrica.zip_cotahist(Path(tmp) / "COTAHIST_A2026.ZIP", linhas)
            conn = db.conectar(":memory:")
            self.addCleanup(conn.close)
            n = b3.gravar(conn, b3.ler_zip(zp, ["02", "12"]))
            self.assertEqual(n, 4)
            tipos = dict(conn.execute("SELECT ticker, tipo FROM ativos"))
            self.assertEqual(tipos, {"PETR4": "acao", "HGLG11": "fii", "SAPR11": "unit"})
            self.assertEqual(conn.execute("SELECT ultima_data FROM ativos WHERE ticker='PETR4'").fetchone()[0],
                             "2026-10-02")
            fech = conn.execute("SELECT fechamento FROM cotacoes WHERE ticker='PETR4' ORDER BY data").fetchall()
            self.assertEqual([round(f[0], 2) for f in fech], [37.00, 38.10])

    def test_regravar_o_mesmo_dia_nao_duplica(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = fabrica.zip_cotahist(Path(tmp) / "a.ZIP", [fabrica.linha_cotahist()])
            conn = db.conectar(":memory:")
            self.addCleanup(conn.close)
            b3.gravar(conn, b3.ler_zip(zp, ["02"]))
            b3.gravar(conn, b3.ler_zip(zp, ["02"]))
            self.assertEqual(conn.execute("SELECT count(*) FROM cotacoes").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

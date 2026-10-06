import unittest
from datetime import date

from radar import db
from radar.fontes import cripto_coingecko as cripto
from radar.fontes import dividendos_yahoo as div
from radar.fontes import macro_bcb as macro
from tests import fabrica


class TestDividendos(unittest.TestCase):
    def test_extrai_e_ordena(self):
        payload = fabrica.json_yahoo({"2026-08-21": 0.71, "2025-11-22": 1.05})
        self.assertEqual(div.extrair_dividendos(payload), [("2025-11-22", 1.05), ("2026-08-21", 0.71)])

    def test_sem_eventos_devolve_lista_vazia(self):
        self.assertEqual(div.extrair_dividendos(fabrica.json_yahoo({})), [])
        self.assertEqual(div.extrair_dividendos({"chart": {"result": None, "error": None}}), [])

    def test_erro_do_servidor_vira_excecao(self):
        payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
        with self.assertRaises(ValueError):
            div.extrair_dividendos(payload)

    def test_selecao_respeita_liquidez_tipo_e_validade(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.executemany("INSERT INTO ativos (ticker, tipo) VALUES (?, ?)",
                         [("LIQD3", "acao"), ("POUC3", "acao"), ("FIIX11", "fii"), ("FEIT4", "acao")])
        for ticker, volume in (("LIQD3", 5e6), ("POUC3", 1e3), ("FIIX11", 9e6), ("FEIT4", 8e6)):
            conn.executemany("INSERT INTO cotacoes (ticker, data, fechamento, volume) VALUES (?, ?, 10, ?)",
                             [(ticker, f"2026-09-{d:02d}", volume) for d in range(1, 31)])
        conn.execute("INSERT INTO dividendos_controle VALUES ('FEIT4', '2026-10-05', 'ok')")
        # media de 60 pregoes: 30 dias com 5 mi = 2,5 mi/dia
        self.assertEqual(div.selecionar_tickers(conn, 500000, date(2026, 10, 6), 7), ["LIQD3"])
        self.assertEqual(div.selecionar_tickers(conn, 500000, date(2026, 10, 20), 7), ["FEIT4", "LIQD3"])


class TestCripto(unittest.TestCase):
    def test_universo_exclui_stablecoins_e_limita(self):
        mercados = fabrica.json_mercados([
            ("bitcoin", "btc", "Bitcoin", 9e12, 430000), ("tether", "usdt", "Tether", 8e11, 5.0),
            ("ethereum", "eth", "Ethereum", 2e12, 15000), ("solana", "sol", "Solana", 5e11, 900),
        ])
        ids = [m["id"] for m in cripto.filtrar_universo(mercados, {"usdt"}, 2)]
        self.assertEqual(ids, ["bitcoin", "ethereum"])

    def test_historico_um_ponto_por_dia(self):
        payload = fabrica.json_historico({"2026-10-04": 100.0, "2026-10-05": 110.0})
        payload["prices"].append([payload["prices"][-1][0] + 3600_000, 112.0])  # segundo ponto do mesmo dia
        linhas = cripto.extrair_historico(payload)
        self.assertEqual([(d, p) for d, p, _, _ in linhas], [("2026-10-04", 100.0), ("2026-10-05", 112.0)])
        self.assertEqual(linhas[0][2], 100.0 * 1e6)

    def test_historico_vazio(self):
        self.assertEqual(cripto.extrair_historico({}), [])


class TestMacro(unittest.TestCase):
    def test_converte_data_e_valor(self):
        payload = [{"data": "17/09/2026", "valor": "13.75"}, {"data": "01/10/2026", "valor": "0,48"}]
        self.assertEqual(macro.extrair_serie(payload), [("2026-09-17", 13.75), ("2026-10-01", 0.48)])

    def test_ignora_itens_invalidos_e_respostas_de_erro(self):
        self.assertEqual(macro.extrair_serie([{"data": "x", "valor": "1"}, {"valor": "2"}]), [])
        self.assertEqual(macro.extrair_serie({"error": "sem dados"}), [])

    def test_visao_macro_atual_ignora_datas_futuras(self):
        conn = db.conectar(":memory:")
        self.addCleanup(conn.close)
        conn.executemany("INSERT INTO macro VALUES (?, ?, ?)",
                         [("selic_meta", "2020-01-01", 4.5), ("selic_meta", "2021-01-01", 2.0),
                          ("selic_meta", "2999-01-01", 99.0)])
        self.assertEqual(conn.execute("SELECT data, valor FROM macro_atual").fetchall(), [("2021-01-01", 2.0)])


if __name__ == "__main__":
    unittest.main()

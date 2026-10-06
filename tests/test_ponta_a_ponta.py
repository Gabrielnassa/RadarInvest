"""Coleta completa contra um servidor local que imita as cinco fontes.

Prova que download, cache, leitura, gravacao, registro da coleta e status funcionam juntos.
Nao prova que os sites reais respondem neste formato: isso so a primeira coleta real mostra.
"""
import contextlib
import functools
import http.server
import io
import os
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

import yaml

from radar import __main__ as cli
from radar import config, db
from radar.rede import Rede
from tests import fabrica

PETRO = "33.000.167/0001-01"
SANEPAR = "76.484.013/0001-45"
HOJE = date(2026, 10, 6)


class Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class TestPontaAPonta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        cls.tmp = tempfile.TemporaryDirectory()
        raiz = Path(cls.tmp.name)
        site = raiz / "site"
        cls.site = site
        L, C = fabrica.linha_cotahist, fabrica.linha_conta

        dias = [f"202609{d:02d}" for d in range(1, 31)] + ["20261001", "20261002"]
        linhas = []
        for d in dias:
            linhas.append(L(data=d, ticker="PETR4", ult=3800))
            linhas.append(L(data=d, ticker="SAPR11", especi="UNT     N2", nome="SANEPAR", ult=3521, isin="BRSAPRCDAM13"))
            linhas.append(L(data=d, ticker="HGLG11", codbdi="12", especi="CI", nome="FII HGLG", ult=14790, isin="BRHGLGCTF004"))
        fabrica.zip_cotahist(site / "b3" / "COTAHIST_A2026.ZIP", linhas)
        fabrica.zip_cotahist(site / "b3" / "COTAHIST_M102026.ZIP",
                             [L(data="20261002", ticker="PETR4", ult=3810), L(data="20261005", ticker="PETR4", ult=4100)])

        fabrica.zip_fca(site / "cvm" / "FCA" / "DADOS" / "fca_cia_aberta_2026.zip", 2026,
                        [[PETRO, "2026-05-30", 1, 1, "PETROLEO BRASILEIRO S.A.", "9512", "Ativo", "Petróleo e Gás"],
                         [SANEPAR, "2026-05-30", 1, 2, "CIA SANEAMENTO DO PARANA", "18627", "Ativo", "Saneamento"]],
                        [[PETRO, "2026-05-30", 1, 1, "PETROBRAS", "Ações Preferenciais", "PETR4", "Bolsa", "", "", ""],
                         [SANEPAR, "2026-05-30", 1, 2, "SANEPAR", "Ações Ordinárias", "SAPR3", "Bolsa", "", "", ""]])
        fabrica.zip_demonstrativos(site / "cvm" / "DFP" / "DADOS" / "dfp_cia_aberta_2025.zip", "dfp", 2025, {
            "DRE_con": [C(PETRO, "2025-12-31", "3.11", "Lucro", 100000, dt_ini="2025-01-01")],
            "BPP_con": [C(PETRO, "2025-12-31", "2.03", "Patrimônio Líquido", 400000)],
            "DRE_ind": [C(SANEPAR, "2025-12-31", "3.11", "Lucro", 1500, dt_ini="2025-01-01")],
        })
        fabrica.zip_demonstrativos(site / "cvm" / "ITR" / "DADOS" / "itr_cia_aberta_2026.zip", "itr", 2026, {
            "DRE_con": [C(PETRO, "2026-06-30", "3.11", "Lucro", 26000, dt_ini="2026-04-01")],
        })

        fabrica.gravar_json(site / "yahoo" / "PETR4.SA", fabrica.json_yahoo({"2026-08-21": 0.71, "2025-11-22": 1.05}))
        fabrica.gravar_json(site / "cg" / "coins" / "markets", fabrica.json_mercados([
            ("bitcoin", "btc", "Bitcoin", 9e12, 430000.0), ("tether", "usdt", "Tether", 8e11, 5.0),
            ("ethereum", "eth", "Ethereum", 2e12, 15000.0)]))
        hist = {"2026-10-04": 425000.0, "2026-10-05": 430000.0}
        fabrica.gravar_json(site / "cg" / "coins" / "bitcoin" / "market_chart", fabrica.json_historico(hist))
        fabrica.gravar_json(site / "cg" / "coins" / "ethereum" / "market_chart", fabrica.json_historico(hist))
        for codigo, valor in ((432, "13.75"), (1, "5.0010")):
            fabrica.gravar_json(site / "bcb" / f"bcdata.sgs.{codigo}" / "dados",
                                [{"data": "02/10/2026", "valor": valor}, {"data": "05/10/2026", "valor": valor}])

        manipulador = functools.partial(Silencioso, directory=str(site))
        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manipulador)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.servidor.server_address[1]}"

        cfg = {
            "banco": "dados/radar.db", "pasta_cache": "dados/cache",
            "rede": {"timeout_segundos": 10, "tentativas": 2},
            "b3": {"url_base": f"{base}/b3", "anos_historico": 2, "codbdi": ["02", "12"]},
            "cvm": {"url_base": f"{base}/cvm", "anos_fundamentos": 1, "nivel_max_conta": 3, "cache_horas": 24},
            "dividendos": {"url_base": f"{base}/yahoo", "anos": 2, "volume_medio_minimo": 1, "pausa_segundos": 0},
            "cripto": {"url_base": f"{base}/cg", "top_n": 2, "excluir_simbolos": ["usdt"], "pausa_segundos": 0},
            "macro": {"url_base": f"{base}/bcb", "anos": 1, "series": {"selic_meta": 432, "dolar_ptax": 1, "ipca_mensal": 433}},
        }
        cls.caminho_cfg = raiz / "config.yaml"
        cls.caminho_cfg.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        cls.cfg = config.carregar(cls.caminho_cfg)

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.tmp.cleanup()

    def _coletar(self, hoje=HOJE):
        rede = Rede(self.cfg["rede"], dormir=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            codigo = cli.cmd_coletar(self.cfg, cli.ORDEM, rede=rede, hoje=hoje)
        return codigo, saida.getvalue()

    def test_1_primeira_coleta(self):
        codigo, saida = self._coletar()
        self.assertEqual(codigo, 0, saida)
        conn = db.conectar(self.cfg["_banco"])
        q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731

        self.assertEqual(q("SELECT count(*) FROM cotacoes")[0][0], 32 * 3)
        self.assertEqual(dict(q("SELECT ticker, tipo FROM ativos")),
                         {"PETR4": "acao", "SAPR11": "unit", "HGLG11": "fii"})
        self.assertEqual(dict(q("SELECT ticker, vinculo FROM ativos")),
                         {"PETR4": "cadastro", "SAPR11": "raiz", "HGLG11": None})
        self.assertEqual(q("SELECT count(*) FROM empresas")[0][0], 2)
        self.assertEqual(sorted(q("SELECT origem, cd_conta, valor, consolidado FROM demonstrativos")), [
            ("DFP", "2.03", 400000000.0, 1), ("DFP", "3.11", 1500000.0, 0),
            ("DFP", "3.11", 100000000.0, 1), ("ITR", "3.11", 26000000.0, 1)])
        self.assertEqual(q("SELECT ticker, data, valor, fonte FROM dividendos ORDER BY data"),
                         [("PETR4", "2025-11-22", 1.05, "yahoo"), ("PETR4", "2026-08-21", 0.71, "yahoo")])
        self.assertEqual([r[0] for r in q("SELECT id FROM cripto_ativos WHERE no_universo = 1 ORDER BY posicao")],
                         ["bitcoin", "ethereum"])
        self.assertEqual(q("SELECT count(*) FROM cripto_cotacoes")[0][0], 4)
        self.assertEqual(dict(q("SELECT serie, valor FROM macro WHERE data = '2026-10-05'")),
                         {"selic_meta": 13.75, "dolar_ptax": 5.001})

        situacoes = dict(q("SELECT fonte, situacao FROM coletas"))
        self.assertEqual(set(situacoes), {"b3", "cvm", "dividendos", "cripto", "macro"})
        self.assertNotIn("erro", situacoes.values())
        avisos = dict(q("SELECT fonte, mensagem FROM coletas"))
        self.assertIn("COTAHIST_A2025.ZIP", avisos["b3"])       # ano sem arquivo vira aviso, nao erro
        self.assertIn("SAPR11", avisos["dividendos"])           # acao sem resposta nao derruba as outras
        conn.close()

    def test_2_segunda_coleta_e_incremental(self):
        cache = Path(self.cfg["_cache"])
        self.assertTrue((cache / "b3" / "COTAHIST_A2026.ZIP").exists())
        codigo, saida = self._coletar()
        self.assertEqual(codigo, 0, saida)
        conn = db.conectar(self.cfg["_banco"])
        # o arquivo mensal traz um pregao novo (05/10) e corrige o fechamento de 02/10
        self.assertEqual(conn.execute("SELECT count(*) FROM cotacoes").fetchone()[0], 32 * 3 + 1)
        self.assertEqual(conn.execute("SELECT fechamento FROM cotacoes WHERE ticker='PETR4' AND data='2026-10-02'")
                         .fetchone()[0], 38.10)
        self.assertEqual(conn.execute("SELECT ultima_data FROM ativos WHERE ticker='PETR4'").fetchone()[0], "2026-10-05")
        self.assertEqual(conn.execute("SELECT count(*) FROM demonstrativos").fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT count(*) FROM dividendos").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT count(*) FROM macro").fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT count(*) FROM coletas").fetchone()[0], 10)
        conn.close()

    def test_3_status_e_fonte_fora_do_ar(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            self.assertEqual(cli.cmd_status(self.cfg), 0)
        texto = saida.getvalue()
        for trecho in ("cotacoes diarias", "ligadas a uma empresa da CVM", "selic_meta", "Ultima coleta"):
            self.assertIn(trecho, texto)

        # uma fonte fora do ar vira erro registrado e as outras continuam
        cfg = dict(self.cfg)
        cfg["cripto"] = dict(cfg["cripto"], url_base="http://127.0.0.1:9/nada")
        rede = Rede({"timeout_segundos": 2, "tentativas": 1}, dormir=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            codigo = cli.cmd_coletar(cfg, ["cripto", "macro"], rede=rede, hoje=HOJE)
        self.assertEqual(codigo, 1)
        conn = db.conectar(self.cfg["_banco"])
        ultimas = dict(conn.execute(
            "SELECT fonte, situacao FROM coletas WHERE id IN (SELECT max(id) FROM coletas GROUP BY fonte)"))
        self.assertEqual(ultimas["cripto"], "erro")
        self.assertIn(ultimas["macro"], ("ok", "avisos"))
        conn.close()


if __name__ == "__main__":
    unittest.main()

"""Linha de comando: python -m radar [coletar | status | diagnostico]."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from . import __version__, config, db
from .fontes import b3_cotahist, cripto_coingecko, cvm, dividendos_yahoo, macro_bcb
from .rede import ErroRede, Rede

FONTES = {
    "b3": ("Cotacoes da B3", b3_cotahist.coletar),
    "cvm": ("Cadastro e demonstrativos da CVM", cvm.coletar),
    "dividendos": ("Dividendos (Yahoo, nao oficial)", dividendos_yahoo.coletar),
    "cripto": ("Criptomoedas (CoinGecko)", cripto_coingecko.coletar),
    "macro": ("Indicadores do Banco Central", macro_bcb.coletar),
}
ORDEM = ["b3", "cvm", "dividendos", "cripto", "macro"]


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(cfg, texto: str) -> None:
    linha = f"[{datetime.now():%H:%M:%S}] {texto}"
    print(linha, flush=True)
    try:
        caminho = Path(cfg["_banco"]).parent / "radar.log"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{date.today().isoformat()} {linha}\n")
    except OSError:
        pass


def cmd_coletar(cfg, fontes: list[str], rede: Rede | None = None, hoje: date | None = None) -> int:
    conn = db.conectar(cfg["_banco"])
    rede = rede or Rede(cfg.get("rede"))
    cache = Path(cfg["_cache"])
    erros = 0
    for nome in [f for f in ORDEM if f in fontes]:
        titulo, funcao = FONTES[nome]
        secao = cfg.get(nome) or {}
        if not secao.get("ativo", True):
            _log(cfg, f"{titulo}: desligado no config.yaml")
            continue
        _log(cfg, f"{titulo}: coletando...")
        inicio = _agora()
        try:
            registros, avisos = funcao(conn, rede, secao, cache, hoje)
            situacao = "avisos" if avisos else "ok"
            mensagem = " | ".join(avisos)
            _log(cfg, f"{titulo}: {registros} registros" + (f" (avisos: {mensagem})" if avisos else ""))
        except Exception as e:
            conn.rollback()
            erros += 1
            registros, situacao, mensagem = 0, "erro", str(e) or type(e).__name__
            _log(cfg, f"{titulo}: ERRO - {mensagem}")
            if not isinstance(e, ErroRede):
                quadro = traceback.extract_tb(e.__traceback__)[-1]
                _log(cfg, f"   ({type(e).__name__} em {Path(quadro.filename).name}, linha {quadro.lineno})")
        conn.execute("INSERT INTO coletas (fonte, inicio, fim, situacao, registros, mensagem) VALUES (?, ?, ?, ?, ?, ?)",
                     (nome, inicio, _agora(), situacao, registros, mensagem[:1000]))
        conn.commit()
    exatos, por_raiz = db.vincular_ativos(conn)
    _log(cfg, f"Acoes ligadas a empresas: {exatos} pelo codigo, {por_raiz} pelas 4 letras iniciais")
    conn.close()
    return 1 if erros else 0


def cmd_status(cfg) -> int:
    conn = db.conectar(cfg["_banco"])
    um = lambda sql, *p: conn.execute(sql, p).fetchone()  # noqa: E731

    print(f"\nRadar de Investimentos {__version__} - situacao do banco")
    print(f"Arquivo: {cfg['_banco']}\n")

    n_cot, d_min, d_max = um("SELECT count(*), min(data), max(data) FROM cotacoes")
    print("B3")
    print(f"  cotacoes diarias ........ {n_cot:>10,}".replace(",", ".") + (f"   ({d_min} a {d_max})" if n_cot else ""))
    for tipo, qtd in conn.execute("SELECT tipo, count(*) FROM ativos GROUP BY tipo ORDER BY count(*) DESC"):
        print(f"  ativos tipo {tipo:<12} {qtd:>10}")
    if d_max:
        acoes, com_cnpj = um(
            """SELECT count(*), sum(cnpj IS NOT NULL) FROM ativos
               WHERE tipo IN ('acao', 'unit') AND ultima_data >= date(?, '-10 day')""", d_max)
        com_dem = um(
            """SELECT count(*) FROM ativos a
               WHERE a.tipo IN ('acao', 'unit') AND a.ultima_data >= date(?, '-10 day')
                 AND EXISTS (SELECT 1 FROM demonstrativos d WHERE d.cnpj = a.cnpj)""", d_max)[0]
        print(f"  acoes/units negociadas nos ultimos 10 dias: {acoes}")
        print(f"    ligadas a uma empresa da CVM ............ {com_cnpj or 0}")
        print(f"    com demonstrativos no banco ............. {com_dem}")

    n_emp = um("SELECT count(*) FROM empresas")[0]
    n_tic = um("SELECT count(*) FROM empresa_tickers")[0]
    n_dem, r_min, r_max = um("SELECT count(*), min(dt_refer), max(dt_refer) FROM demonstrativos")
    n_cia = um("SELECT count(DISTINCT cnpj) FROM demonstrativos")[0]
    print("\nCVM")
    print(f"  empresas no cadastro .... {n_emp:>10}")
    print(f"  codigos de negociacao ... {n_tic:>10}")
    print(f"  linhas de demonstrativos  {n_dem:>10,}".replace(",", ".") + (f"   ({r_min} a {r_max}, {n_cia} empresas)" if n_dem else ""))

    n_div, n_div_t = um("SELECT count(*), count(DISTINCT ticker) FROM dividendos")
    print("\nDividendos")
    print(f"  pagamentos .............. {n_div:>10}   ({n_div_t} acoes)")

    n_cri = um("SELECT count(*) FROM cripto_ativos WHERE no_universo = 1")[0]
    n_cc, c_min, c_max = um("SELECT count(*), min(data), max(data) FROM cripto_cotacoes")
    print("\nCripto")
    print(f"  moedas no universo ...... {n_cri:>10}")
    print(f"  cotacoes diarias ........ {n_cc:>10}" + (f"   ({c_min} a {c_max})" if n_cc else ""))

    print("\nBanco Central (valor mais recente)")
    linhas = conn.execute("SELECT serie, data, valor FROM macro_atual ORDER BY serie").fetchall()
    for serie, dia, valor in linhas:
        print(f"  {serie:<14} {valor:>10.4f}   em {dia}")
    if not linhas:
        print("  sem dados")

    print("\nUltima coleta de cada fonte")
    ultimas = conn.execute(
        """SELECT fonte, fim, situacao, registros, mensagem FROM coletas
           WHERE id IN (SELECT max(id) FROM coletas GROUP BY fonte) ORDER BY fonte""").fetchall()
    for fonte, fim, situacao, registros, mensagem in ultimas:
        print(f"  {fonte:<11} {fim}  {situacao:<7} {registros} registros" + (f"\n      {mensagem}" if mensagem else ""))
    if not ultimas:
        print("  nenhuma coleta feita ainda")
    print()
    conn.close()
    return 0


def cmd_diagnostico(cfg) -> int:
    """Faz uma chamada pequena a cada fonte e mostra o que respondeu."""
    rede = Rede(cfg.get("rede"))
    hoje = date.today()
    testes = [
        ("B3 (cotacoes)", f"{cfg['b3']['url_base'].rstrip('/')}/COTAHIST_M{hoje.month:02d}{hoje.year}.ZIP", None),
        ("CVM (cadastro)", f"{cfg['cvm']['url_base'].rstrip('/')}/FCA/DADOS/fca_cia_aberta_{hoje.year}.zip", None),
        ("CVM (demonstrativos)", f"{cfg['cvm']['url_base'].rstrip('/')}/ITR/DADOS/itr_cia_aberta_{hoje.year}.zip", None),
        ("Yahoo (dividendos)", f"{cfg['dividendos']['url_base'].rstrip('/')}/PETR4.SA", {"range": "1mo", "interval": "1d", "events": "div"}),
        ("CoinGecko (cripto)", f"{cfg['cripto']['url_base'].rstrip('/')}/ping", None),
        ("Banco Central", f"{cfg['macro']['url_base'].rstrip('/')}/bcdata.sgs.432/dados/ultimos/1", {"formato": "json"}),
    ]
    print("\nDiagnostico das fontes de dados\n")
    falhas = 0
    for nome, url, params in testes:
        verificar = bool(cfg["b3"].get("verificar_certificado", True)) if nome.startswith("B3") else True
        ok, detalhe = rede.testar(url, params=params, verificar=verificar)
        falhas += 0 if ok else 1
        print(f"  {'OK   ' if ok else 'FALHA'}  {nome:<22} {detalhe}")
        if not ok:
            print(f"         {url}")
    print("\nTodas as fontes responderam." if not falhas else
          f"\n{falhas} fonte(s) com problema. Copie esta tela para quem for corrigir.")
    return 1 if falhas else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Radar de Investimentos - coleta de dados")
    p.add_argument("--config", help="caminho do config.yaml")
    sub = p.add_subparsers(dest="comando")
    c = sub.add_parser("coletar", help="baixa e grava os dados")
    c.add_argument("--fonte", action="append", choices=ORDEM, help="coleta so esta fonte (pode repetir)")
    sub.add_parser("status", help="mostra o que ha no banco")
    sub.add_parser("diagnostico", help="testa o acesso a cada fonte")
    args = p.parse_args(argv)

    config.carregar_env()
    cfg = config.carregar(args.config)
    if args.comando == "coletar":
        return cmd_coletar(cfg, args.fonte or ORDEM)
    if args.comando == "diagnostico":
        return cmd_diagnostico(cfg)
    return cmd_status(cfg)


if __name__ == "__main__":
    sys.exit(main())

"""Linha de comando: python -m radar [coletar | status | diagnostico]."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from . import __version__, config, db, exportar
from .fontes import b3_cotahist, cripto_coingecko, cvm, cvm_fii, dividendos_yahoo, macro_bcb, tesouro
from .rede import ErroRede, Rede

FONTES = {
    "b3": ("Cotacoes da B3", b3_cotahist.coletar),
    "cvm": ("Cadastro e demonstrativos da CVM", cvm.coletar),
    "dividendos": ("Dividendos (Yahoo, nao oficial)", dividendos_yahoo.coletar),
    "cripto": ("Criptomoedas (CoinGecko)", cripto_coingecko.coletar),
    "macro": ("Indicadores do Banco Central", macro_bcb.coletar),
    "fii": ("Informe mensal dos fundos imobiliarios (CVM)", cvm_fii.coletar),
    "tesouro": ("Tesouro Direto (Tesouro Transparente)", tesouro.coletar),
}
ORDEM = ["b3", "cvm", "dividendos", "cripto", "macro", "fii", "tesouro"]


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
        ("B3 (cotacoes)", f"{cfg['b3']['url_base'].rstrip('/')}/COTAHIST_A{hoje.year}.ZIP", None),
        ("CVM (cadastro)", f"{cfg['cvm']['url_base'].rstrip('/')}/FCA/DADOS/fca_cia_aberta_{hoje.year}.zip", None),
        ("CVM (demonstrativos)", f"{cfg['cvm']['url_base'].rstrip('/')}/ITR/DADOS/itr_cia_aberta_{hoje.year}.zip", None),
        ("Yahoo (dividendos)", f"{cfg['dividendos']['url_base'].rstrip('/')}/PETR4.SA", {"range": "1mo", "interval": "1d", "events": "div"}),
        ("CoinGecko (cripto)", f"{cfg['cripto']['url_base'].rstrip('/')}/ping", None),
        ("Banco Central", f"{cfg['macro']['url_base'].rstrip('/')}/bcdata.sgs.432/dados/ultimos/1", {"formato": "json"}),
    ]
    if cfg.get("fii"):
        testes.append(("CVM (FII)", f"{cfg['fii']['url_base'].rstrip('/')}/inf_mensal_fii_{hoje.year}.zip", None))
    if cfg.get("tesouro"):
        testes.append(("Tesouro Direto", cfg["tesouro"]["url"], None))
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


def cmd_inspecionar(cfg) -> int:
    """Mostra os arquivos baixados por dentro: nomes, cabecalhos e uma linha de exemplo."""
    import json
    import zipfile
    cache = Path(cfg["_cache"])
    vistos = set()
    for caminho in sorted((cache / "cvm").glob("*.zip"), reverse=True):
        tipo = caminho.name.split("_")[0]
        if tipo in vistos:
            continue
        vistos.add(tipo)
        print(f"\n=== {caminho.name} ({caminho.stat().st_size // 1024} KB)")
        try:
            with zipfile.ZipFile(caminho) as zf:
                for nome in zf.namelist():
                    with zf.open(nome) as f:
                        cab = f.readline().decode("latin-1").strip()
                        linha = f.readline().decode("latin-1").strip()
                    print(f"- {nome}\n    colunas: {cab[:600]}\n    exemplo: {linha[:300]}")
        except Exception as e:
            print(f"  erro ao abrir: {e}")
    for caminho in sorted((cache / "b3").glob("*.ZIP"), reverse=True)[:1]:
        print(f"\n=== {caminho.name} ({caminho.stat().st_size // 1024} KB)")
        with zipfile.ZipFile(caminho) as zf:
            print("  membros:", zf.namelist())
            with zf.open(zf.namelist()[0]) as f:
                for i, bruto in enumerate(f):
                    if i < 2 or (bruto[12:17] in (b"PETR4", b"SAPR1") and bruto[10:12] == b"02" and i % 50 == 0):
                        print("  " + bruto.decode("latin-1").rstrip()[:245])
                    if i > 400000:
                        break
    rede = Rede(cfg.get("rede"))
    amostras = [
        ("Yahoo", f"{cfg['dividendos']['url_base'].rstrip('/')}/PETR4.SA", {"range": "1y", "interval": "1mo", "events": "div"}),
        ("CoinGecko", f"{cfg['cripto']['url_base'].rstrip('/')}/coins/markets", {"vs_currency": "brl", "per_page": 2, "page": 1}),
        ("BCB", f"{cfg['macro']['url_base'].rstrip('/')}/bcdata.sgs.432/dados/ultimos/3", {"formato": "json"}),
    ]
    for nome, url, params in amostras:
        print(f"\n=== {nome}: {url}")
        try:
            r = rede.sessao.get(url, params=params, timeout=30)
            print(f"  HTTP {r.status_code}")
            texto = r.text
            try:
                dado = r.json()
                if nome == "Yahoo":
                    res = (dado.get("chart", {}).get("result") or [{}])[0]
                    dado = {"chaves": list(res.keys()), "meta_moeda": res.get("meta", {}).get("currency"),
                            "eventos": res.get("events"), "erro": dado.get("chart", {}).get("error")}
                texto = json.dumps(dado, ensure_ascii=False)
            except ValueError:
                pass
            print("  " + texto[:1500])
        except Exception as e:
            print(f"  falhou: {type(e).__name__}: {str(e)[:200]}")
    return 0


def cmd_painel(cfg, porta: int = 8765) -> int:
    """Abre o painel no navegador, servindo a pasta docs/ neste computador."""
    import functools
    import http.server
    import webbrowser
    pasta = Path(cfg["_banco"]).parent.parent / "docs"
    if not (pasta / "dados.json").exists():
        print("Ainda nao ha dados. Rode antes: python -m radar coletar  e  python -m radar exportar")
        return 1
    manipulador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pasta))
    try:
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", porta), manipulador)
    except OSError:
        print(f"A porta {porta} ja esta em uso. O painel pode ja estar aberto em http://127.0.0.1:{porta}/")
        return 1
    endereco = f"http://127.0.0.1:{porta}/"
    print(f"Painel em {endereco}  (feche esta janela para encerrar)")
    webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Radar de Investimentos - coleta de dados")
    p.add_argument("--config", help="caminho do config.yaml")
    sub = p.add_subparsers(dest="comando")
    c = sub.add_parser("coletar", help="baixa e grava os dados")
    c.add_argument("--fonte", action="append", choices=ORDEM, help="coleta so esta fonte (pode repetir)")
    sub.add_parser("status", help="mostra o que ha no banco")
    sub.add_parser("diagnostico", help="testa o acesso a cada fonte")
    sub.add_parser("inspecionar", help="mostra o formato dos arquivos baixados")
    sub.add_parser("exportar", help="calcula as notas e grava docs/dados.json para o painel")
    sub.add_parser("painel", help="abre o painel no navegador")
    args = p.parse_args(argv)

    config.carregar_env()
    cfg = config.carregar(args.config)
    if args.comando == "coletar":
        return cmd_coletar(cfg, args.fonte or ORDEM)
    if args.comando == "diagnostico":
        return cmd_diagnostico(cfg)
    if args.comando == "inspecionar":
        return cmd_inspecionar(cfg)
    if args.comando == "painel":
        return cmd_painel(cfg)
    if args.comando == "exportar":
        destino, dados = exportar.exportar(cfg)
        print(f"{len(dados['acoes'])} acoes, {len(dados.get('fiis') or [])} FIIs e {len(dados['cripto'])} criptos gravados em {destino}")
        bt = dados.get("backtest") or {}
        print(f"Backtest: {len(bt.get('periodos') or [])} periodos; acumulado {bt.get('acumulado')}")
        for a in dados["acoes"][:10]:
            print(f"  {a['t']:<7} nota {a['final']:5.1f}  P/L {a['pl']}  P/VP {a['pvp']}  DY {a['dy']}  {a['m']}")
        return 0
    return cmd_status(cfg)


if __name__ == "__main__":
    sys.exit(main())

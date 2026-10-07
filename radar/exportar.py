"""Gera o arquivo que o painel le (docs/dados.json) a partir do banco."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from datetime import date, timedelta

from . import backtest, db, notas


def _limpar(x):
    """Arredonda numeros e troca NaN/infinito por None, para o JSON ficar valido e pequeno."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 4) if abs(x) < 1000 else round(x, 2)
    if isinstance(x, dict):
        return {k: _limpar(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_limpar(v) for v in x]
    return x


def macro(conn) -> dict:
    saida = {}
    for serie, dia, valor in conn.execute("SELECT serie, data, valor FROM macro_atual"):
        saida[serie] = {"valor": valor, "data": dia}
    ipca = [v for (v,) in conn.execute(
        "SELECT valor FROM macro WHERE serie = 'ipca_mensal' AND data <= date('now') ORDER BY data DESC LIMIT 12")]
    if len(ipca) == 12:
        acumulado = 1.0
        for v in ipca:
            acumulado *= 1 + v / 100
        saida["ipca_12m"] = {"valor": (acumulado - 1) * 100, "data": saida.get("ipca_mensal", {}).get("data")}
    return saida


def coletas(conn) -> list[dict]:
    return [{"fonte": f, "fim": fim, "situacao": s, "registros": r, "mensagem": m or ""}
            for f, fim, s, r, m in conn.execute(
                """SELECT fonte, fim, situacao, registros, mensagem FROM coletas
                   WHERE id IN (SELECT max(id) FROM coletas GROUP BY fonte) ORDER BY fonte""")]


def sem_vinculo(conn, volume_minimo: float) -> list[str]:
    """Acoes com liquidez que ficaram fora do ranking por nao estarem ligadas a uma empresa da CVM."""
    return [t for (t,) in conn.execute(
        """WITH ultimos AS (SELECT DISTINCT data FROM cotacoes ORDER BY data DESC LIMIT 60)
           SELECT a.ticker FROM ativos a JOIN cotacoes c ON c.ticker = a.ticker
           WHERE a.tipo IN ('acao', 'unit') AND a.cnpj IS NULL AND c.data IN (SELECT data FROM ultimos)
           GROUP BY a.ticker HAVING sum(c.volume) / 60.0 >= ? ORDER BY sum(c.volume) DESC""", (volume_minimo,))]


def precos_mensais(conn, tickers: list[str], anos: int = 5) -> dict[str, list]:
    """Ultimo fechamento de cada mes nos ultimos `anos`, na base de acoes de hoje: {ticker: [[aaaa-mm, preco]]}."""
    ultima = conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0]
    if not ultima or not tickers:
        return {}
    desde = (date.fromisoformat(ultima) - timedelta(days=365 * anos + 31)).isoformat()
    splits: dict[str, list] = {}
    for t, d, f in conn.execute("SELECT ticker, data, fator FROM desdobramentos WHERE fator > 0"):
        splits.setdefault(t, []).append((d, f))
    saida: dict[str, dict] = {}
    marcas = ",".join("?" * len(tickers))
    for t, d, fech in conn.execute(f"SELECT ticker, data, fechamento FROM cotacoes WHERE ticker IN ({marcas}) AND data >= ? "
                                   "ORDER BY ticker, data", (*tickers, desde)):
        if fech:
            fator = 1.0
            for quando, f in splits.get(t, ()):
                if quando > d:
                    fator *= f
            saida.setdefault(t, {})[d[:7]] = round(fech / fator, 2)
    return {t: [[m, v] for m, v in sorted(meses.items())] for t, meses in saida.items()}


def tesouro(conn) -> dict:
    """Titulos a venda na data mais recente, com a taxa de um ano antes para comparar."""
    ultima = conn.execute("SELECT max(data) FROM tesouro").fetchone()[0]
    if not ultima:
        return {"data": None, "titulos": []}
    ano_antes = (date.fromisoformat(ultima) - timedelta(days=365)).isoformat()
    titulos = []
    for tit, venc, tc, tv, pc, pv in conn.execute(
            "SELECT titulo, vencimento, taxa_compra, taxa_venda, pu_compra, pu_venda FROM tesouro WHERE data = ? ORDER BY titulo, vencimento",
            (ultima,)):
        antes = conn.execute("SELECT taxa_venda FROM tesouro WHERE titulo = ? AND vencimento = ? AND data <= ? ORDER BY data DESC LIMIT 1",
                             (tit, venc, ano_antes)).fetchone()
        serie = [[d[:7], v] for d, v in conn.execute(
            """SELECT max(data), taxa_venda FROM tesouro WHERE titulo = ? AND vencimento = ? GROUP BY substr(data, 1, 7) ORDER BY 1""",
            (tit, venc))]
        titulos.append({"titulo": tit, "venc": venc, "taxaCompra": tc, "taxaVenda": tv, "puCompra": pc, "puVenda": pv,
                        "aVenda": bool(tc and tc > 0 and pc and pc > 0), "taxaAnoAntes": antes[0] if antes else None,
                        "serie": serie})
    return {"data": ultima, "titulos": titulos}


def montar(conn, cfg: dict) -> dict:
    regras = cfg.get("notas") or {}
    acoes = notas.calcular_acoes(conn, regras)
    cripto = notas.calcular_cripto(conn, regras)
    fiis = notas.calcular_fiis(conn, regras)
    teste = backtest.rodar(conn, regras)
    historico = teste.pop("historico", {})
    mensais = precos_mensais(conn, [a["t"] for a in acoes])
    for a in acoes:
        a["hist"] = historico.get(a["t"], []) + [[a["data"], round(a["final"]), a.get("lp")]]
        a["precos5"] = mensais.get(a["t"], [])
    return _limpar({
        "geradoEm": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataCotacao": conn.execute("SELECT max(data) FROM cotacoes").fetchone()[0],
        "pesos": dict(notas.PADRAO["pesos"], **(regras.get("pesos") or {})),
        "acoes": acoes,
        "cripto": cripto,
        "fiis": fiis,
        "tesouro": tesouro(conn),
        "backtest": teste,
        "macro": macro(conn),
        "coleta": coletas(conn),
        "semVinculo": sem_vinculo(conn, float(regras.get("volume_minimo", notas.PADRAO["volume_minimo"]))),
    })


def exportar(cfg: dict, destino: Path | None = None) -> tuple[Path, dict]:
    destino = Path(destino or (Path(cfg["_banco"]).parent.parent / "docs" / "dados.json"))
    conn = db.conectar(cfg["_banco"])
    try:
        dados = montar(conn, cfg)
    finally:
        conn.close()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return destino, dados

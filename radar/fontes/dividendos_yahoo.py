"""Dividendos e JCP por acao, lidos do Yahoo Finance (fonte NAO oficial).

A B3 e a CVM nao publicam um arquivo unico com o historico de proventos por acao. O Yahoo
serve esse historico de graca, mas sem garantia: pode mudar ou bloquear sem aviso, e os
valores vem ajustados por desdobramentos. Por isso cada linha grava a fonte.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FONTE = "yahoo"
FEITO = "ok-2"   # marca de consulta concluida; muda quando a forma de consultar muda, para refazer tudo


def extrair_dividendos(payload: dict) -> list[tuple[str, float]]:
    """Devolve [(data ISO, valor por acao)] a partir da resposta do endpoint /v8/finance/chart."""
    chart = (payload or {}).get("chart") or {}
    erro = chart.get("error")
    if erro:
        raise ValueError(str(erro.get("description") or erro.get("code") or erro))
    resultado = chart.get("result") or []
    if not resultado:
        return []
    eventos = (resultado[0].get("events") or {}).get("dividends") or {}
    saida: dict[str, float] = {}
    for item in eventos.values():
        ts, valor = item.get("date"), item.get("amount")
        if ts is None or valor is None:
            continue
        dia = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        saida[dia] = saida.get(dia, 0.0) + float(valor)
    return sorted(saida.items())


def extrair_desdobramentos(payload: dict) -> list[tuple[str, float]]:
    """Devolve [(data ISO, acoes depois / acoes antes)] de desdobramentos, bonificacoes e grupamentos."""
    resultado = ((payload or {}).get("chart") or {}).get("result") or []
    if not resultado:
        return []
    eventos = (resultado[0].get("events") or {}).get("splits") or {}
    saida = []
    for item in eventos.values():
        ts, num, den = item.get("date"), item.get("numerator"), item.get("denominator")
        if ts is None or not num or not den:
            continue
        saida.append((datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(), float(num) / float(den)))
    return sorted(saida)


def selecionar_tickers(conn, volume_minimo: float, hoje: date, dias_validade: int) -> list[str]:
    """Acoes, units e fundos imobiliarios com liquidez minima que ainda nao foram atualizadas dentro da validade."""
    limite = (hoje - timedelta(days=dias_validade)).isoformat()
    linhas = conn.execute(
        """WITH ultimos AS (SELECT DISTINCT data FROM cotacoes ORDER BY data DESC LIMIT 60)
           SELECT a.ticker
           FROM ativos a JOIN cotacoes c ON c.ticker = a.ticker
           WHERE a.tipo IN ('acao', 'unit', 'fii') AND c.data IN (SELECT data FROM ultimos)
           GROUP BY a.ticker
           HAVING sum(c.volume) / 60.0 >= ?
           ORDER BY sum(c.volume) DESC""",
        (volume_minimo,),
    ).fetchall()
    feitos = {t for (t,) in conn.execute(
        "SELECT ticker FROM dividendos_controle WHERE atualizado_em > ? AND situacao = ?", (limite, FEITO))}
    return [t for (t,) in linhas if t not in feitos]


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    anos = int(cfg.get("anos", 6))
    pausa = float(cfg.get("pausa_segundos", 0.5))
    tickers = selecionar_tickers(conn, float(cfg.get("volume_medio_minimo", 500000)), hoje,
                                 int(cfg.get("atualizar_apos_dias", 7)))
    if not tickers:
        return 0, []

    inicio = datetime(hoje.year - anos, 1, 1, tzinfo=timezone.utc)
    fim = datetime(hoje.year, hoje.month, hoje.day, tzinfo=timezone.utc) + timedelta(days=1)
    params = {"period1": int(inicio.timestamp()), "period2": int(fim.timestamp()),
              "interval": "1d", "events": "div|split"}

    total, falhas, primeira_falha = 0, 0, ""
    for n, ticker in enumerate(tickers, start=1):
        situacao = FEITO
        try:
            _, payload = rede.json(f"{base}/{ticker}.SA", params=params, aceitar=(200, 404))
            linhas = extrair_dividendos(payload)
            conn.execute("DELETE FROM dividendos WHERE ticker = ? AND fonte = ?", (ticker, FONTE))
            conn.executemany("INSERT OR REPLACE INTO dividendos (ticker, data, valor, fonte) VALUES (?, ?, ?, ?)",
                             [(ticker, d, v, FONTE) for d, v in linhas])
            conn.execute("DELETE FROM desdobramentos WHERE ticker = ? AND fonte = ?", (ticker, FONTE))
            conn.executemany("INSERT OR REPLACE INTO desdobramentos (ticker, data, fator, fonte) VALUES (?, ?, ?, ?)",
                             [(ticker, d, f, FONTE) for d, f in extrair_desdobramentos(payload)])
            total += len(linhas)
        except Exception as e:  # uma acao com problema nao pode parar as outras
            situacao = "erro"
            falhas += 1
            primeira_falha = primeira_falha or f"{ticker}: {e}"
        conn.execute("INSERT OR REPLACE INTO dividendos_controle (ticker, atualizado_em, situacao) VALUES (?, ?, ?)",
                     (ticker, hoje.isoformat(), situacao))
        conn.commit()
        if n == 5 and falhas == 5:
            raise RuntimeError(f"as 5 primeiras consultas falharam; a fonte pode estar bloqueada ({primeira_falha})")
        rede.dormir(pausa)

    avisos = [f"{falhas} de {len(tickers)} acoes sem resposta (ex.: {primeira_falha})"] if falhas else []
    return total, avisos

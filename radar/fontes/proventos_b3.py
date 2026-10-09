"""Proventos em dinheiro informados a B3 (dividendos, JCP e rendimentos), pela consulta publica de
empresas listadas (sistemaswebb3-listados.b3.com.br).

E a mesma consulta que alimenta a pagina "Eventos corporativos" de cada empresa no site da B3. A
consulta recebe o codigo de 4 letras da empresa em JSON codificado em base64 e devolve os proventos
com o ISIN do papel, a data com (ultimo dia com direito), a data de pagamento e o valor por acao
na data do anuncio (sem ajuste por desdobramentos posteriores; o ajuste e feito no calculo).

Nao e um arquivo de dados abertos: se a B3 mudar a consulta, o painel volta a usar o Yahoo.
"""
from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from pathlib import Path

FEITO = "ok"


def _numero(txt) -> float | None:
    t = str(txt or "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _data(txt) -> str | None:
    t = str(txt or "").strip()[:10]
    if len(t) == 10 and t[2] == "/" and t[5] == "/":
        return f"{t[6:10]}-{t[3:5]}-{t[0:2]}"
    if len(t) == 10 and t[4] == "-":
        return t
    return None


def url_consulta(base: str, raiz: str) -> str:
    chave = base64.b64encode(json.dumps({"issuingCompany": raiz, "language": "pt-br"}).encode()).decode()
    return f"{base.rstrip('/')}/GetListedSupplementCompany/{chave}"


def extrair(payload) -> list[dict]:
    """Devolve [{isin, data_com, pagamento, aprovado, tipo, valor}] da resposta da B3."""
    blocos = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    saida = []
    for bloco in blocos:
        for item in (bloco or {}).get("cashDividends") or []:
            valor = _numero(item.get("rate"))
            data_com = _data(item.get("lastDatePrior"))
            isin = str(item.get("isinCode") or item.get("assetIssued") or "").strip().upper()
            if not valor or valor <= 0 or not data_com or not isin:
                continue
            saida.append({"isin": isin, "data_com": data_com, "pagamento": _data(item.get("paymentDate")),
                          "aprovado": _data(item.get("approvedOn")), "tipo": str(item.get("label") or "").strip().upper()[:40],
                          "valor": valor})
    return saida


def selecionar_raizes(conn, volume_minimo: float, hoje: date, dias_validade: int) -> list[str]:
    """Codigos de 4 letras das empresas com acoes ou units liquidas, sem consulta dentro da validade."""
    limite = (hoje - timedelta(days=dias_validade)).isoformat()
    linhas = conn.execute(
        """WITH ultimos AS (SELECT DISTINCT data FROM cotacoes ORDER BY data DESC LIMIT 60)
           SELECT a.ticker FROM ativos a JOIN cotacoes c ON c.ticker = a.ticker
           WHERE a.tipo IN ('acao', 'unit') AND c.data IN (SELECT data FROM ultimos)
           GROUP BY a.ticker HAVING sum(c.volume) / 60.0 >= ? ORDER BY sum(c.volume) DESC""", (volume_minimo,)).fetchall()
    feitas = {r for (r,) in conn.execute(
        "SELECT raiz FROM proventos_b3_controle WHERE atualizado_em > ? AND situacao = ?", (limite, FEITO))}
    raizes = []
    for (t,) in linhas:
        if t[:4] not in raizes and t[:4] not in feitas:
            raizes.append(t[:4])
    return raizes


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    raizes = selecionar_raizes(conn, float(cfg.get("volume_medio_minimo", 500000)), hoje, int(cfg.get("atualizar_apos_dias", 7)))
    if not raizes:
        return 0, []
    por_isin = {i: t for t, i in conn.execute("SELECT ticker, isin FROM ativos WHERE isin IS NOT NULL AND tipo IN ('acao', 'unit')")}
    pausa = float(cfg.get("pausa_segundos", 0.5))
    total, falhas, primeira = 0, 0, ""
    for n, raiz in enumerate(raizes, start=1):
        situacao = FEITO
        try:
            _, payload = rede.json(url_consulta(cfg["url_base"], raiz))
            linhas = [x for x in extrair(payload) if x["isin"] in por_isin]
            tickers = {por_isin[x["isin"]] for x in linhas}
            for t in tickers:
                conn.execute("DELETE FROM proventos_b3 WHERE ticker = ?", (t,))
            conn.executemany(
                """INSERT OR REPLACE INTO proventos_b3 (ticker, data_com, tipo, valor, pagamento, aprovado)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(por_isin[x["isin"]], x["data_com"], x["tipo"], x["valor"], x["pagamento"], x["aprovado"]) for x in linhas])
            total += len(linhas)
        except Exception as e:  # uma empresa com problema nao pode parar as outras
            situacao = "erro"
            falhas += 1
            primeira = primeira or f"{raiz}: {e}"
        conn.execute("INSERT OR REPLACE INTO proventos_b3_controle (raiz, atualizado_em, situacao) VALUES (?, ?, ?)",
                     (raiz, hoje.isoformat(), situacao))
        conn.commit()
        if n == 5 and falhas == 5:
            raise RuntimeError(f"as 5 primeiras consultas falharam; a B3 pode ter mudado a consulta ({primeira}). O painel segue com o Yahoo")
        rede.dormir(pausa)
    return total, ([f"{falhas} de {len(raizes)} empresas sem resposta (ex.: {primeira})"] if falhas else [])

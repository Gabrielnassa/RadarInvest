"""Criptomoedas pela API publica da CoinGecko.

Sem chave a API aceita poucas chamadas por minuto, por isso ha uma pausa entre as moedas.
Com uma chave de demonstracao gratuita (COINGECKO_API_KEY no .env) a pausa pode ser menor.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path


def _cabecalhos() -> dict:
    chave = os.environ.get("COINGECKO_API_KEY", "").strip()
    return {"x-cg-demo-api-key": chave} if chave else {}


def filtrar_universo(mercados: list[dict], excluir: set[str], top_n: int) -> list[dict]:
    """Tira stablecoins e moedas 'embrulhadas' e fica com as top_n por valor de mercado."""
    validos = [m for m in mercados
               if m.get("id") and (m.get("symbol") or "").lower() not in excluir and m.get("market_cap")]
    validos.sort(key=lambda m: m["market_cap"], reverse=True)
    return validos[:top_n]


def extrair_historico(payload: dict) -> list[tuple[str, float, float | None, float | None]]:
    """Devolve [(data ISO, preco, valor de mercado, volume)], um ponto por dia (o ultimo do dia)."""
    def por_dia(serie):
        dias: dict[str, float] = {}
        for ponto in serie or []:
            if len(ponto) >= 2 and ponto[1] is not None:
                dia = datetime.fromtimestamp(ponto[0] / 1000, tz=timezone.utc).date().isoformat()
                dias[dia] = float(ponto[1])
        return dias

    precos = por_dia((payload or {}).get("prices"))
    caps = por_dia((payload or {}).get("market_caps"))
    volumes = por_dia((payload or {}).get("total_volumes"))
    return [(d, precos[d], caps.get(d), volumes.get(d)) for d in sorted(precos)]


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    moeda = cfg.get("moeda", "brl")
    top_n = int(cfg.get("top_n", 30))
    excluir = {s.lower() for s in cfg.get("excluir_simbolos", [])}
    pausa = float(cfg.get("pausa_segundos", 6))
    cab = _cabecalhos()

    _, mercados = rede.json(f"{base}/coins/markets", headers=cab, params={
        "vs_currency": moeda, "order": "market_cap_desc", "per_page": min(250, top_n + 40), "page": 1})
    if not isinstance(mercados, list):
        raise RuntimeError(f"resposta inesperada da CoinGecko: {str(mercados)[:160]}")
    universo = filtrar_universo(mercados, excluir, top_n)
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn.execute("UPDATE cripto_ativos SET no_universo = 0")
    conn.executemany(
        """INSERT OR REPLACE INTO cripto_ativos
           (id, simbolo, nome, posicao, preco, valor_mercado, volume_24h, maxima_historica, dist_maxima_pct,
            data_maxima, oferta_circulante, moeda, no_universo, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        [(m["id"], m.get("symbol"), m.get("name"), m.get("market_cap_rank"), m.get("current_price"),
          m.get("market_cap"), m.get("total_volume"), m.get("ath"), m.get("ath_change_percentage"),
          (m.get("ath_date") or "")[:10], m.get("circulating_supply"), moeda, agora) for m in universo],
    )
    conn.commit()

    total, falhas, primeira_falha = len(universo), 0, ""
    dias_cfg = int(cfg.get("dias_historico", 365))
    for m in universo:
        ultima = conn.execute("SELECT max(data) FROM cripto_cotacoes WHERE id = ?", (m["id"],)).fetchone()[0]
        if ultima:
            falta = (hoje - date.fromisoformat(ultima)).days
            if falta <= 0:
                continue
            dias = min(dias_cfg, falta + 2)
        else:
            dias = dias_cfg
        rede.dormir(pausa)
        try:
            _, payload = rede.json(f"{base}/coins/{m['id']}/market_chart", headers=cab,
                                   params={"vs_currency": moeda, "days": dias})
            linhas = extrair_historico(payload)
            conn.executemany(
                "INSERT OR REPLACE INTO cripto_cotacoes (id, data, preco, valor_mercado, volume) VALUES (?, ?, ?, ?, ?)",
                [(m["id"], d, p, c, v) for d, p, c, v in linhas])
            conn.commit()
            total += len(linhas)
        except Exception as e:
            falhas += 1
            primeira_falha = primeira_falha or f"{m['id']}: {e}"

    avisos = [f"{falhas} de {len(universo)} moedas sem historico (ex.: {primeira_falha})"] if falhas else []
    return total, avisos

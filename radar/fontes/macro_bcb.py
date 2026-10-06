"""Indicadores do Banco Central pelo Sistema Gerenciador de Series Temporais (SGS)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path


def extrair_serie(payload) -> list[tuple[str, float]]:
    """Converte [{'data': 'dd/mm/aaaa', 'valor': '13.75'}] em [(data ISO, valor)]."""
    if not isinstance(payload, list):
        return []
    saida = []
    for item in payload:
        try:
            dia, mes, ano = str(item["data"]).split("/")
            valor = float(str(item["valor"]).replace(",", "."))
        except (KeyError, ValueError, TypeError):
            continue
        saida.append((f"{ano}-{mes}-{dia}", valor))
    return saida


def coletar(conn, rede, cfg, cache: Path, hoje: date | None = None):
    hoje = hoje or date.today()
    base = cfg["url_base"].rstrip("/")
    anos = min(10, int(cfg.get("anos", 10)))  # o SGS limita series diarias a janelas de 10 anos
    total, avisos = 0, []
    for nome, codigo in (cfg.get("series") or {}).items():
        ultima = conn.execute("SELECT max(data) FROM macro WHERE serie = ?", (nome,)).fetchone()[0]
        if ultima:
            inicio = date.fromisoformat(ultima) + timedelta(days=1)
        else:
            inicio = hoje - timedelta(days=365 * anos)
        if inicio > hoje:
            continue
        try:
            status, payload = rede.json(
                f"{base}/bcdata.sgs.{codigo}/dados",
                params={"formato": "json", "dataInicial": inicio.strftime("%d/%m/%Y"),
                        "dataFinal": hoje.strftime("%d/%m/%Y")},
                aceitar=(200, 404),
            )
        except Exception as e:
            avisos.append(f"{nome} (serie {codigo}): {e}")
            continue
        linhas = extrair_serie(payload) if status == 200 else []
        conn.executemany("INSERT OR REPLACE INTO macro (serie, data, valor) VALUES (?, ?, ?)",
                         [(nome, d, v) for d, v in linhas])
        conn.commit()
        total += len(linhas)
    return total, avisos

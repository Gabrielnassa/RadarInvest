"""Alertas por Telegram: mudancas de nota, fatos relevantes e problemas na coleta.

So roda com TELEGRAM_TOKEN e TELEGRAM_CHAT_ID definidos (no GitHub: Settings > Secrets > Actions).
Os codigos acompanhados ficam em `alertas.tickers` no config.yaml (a carteira do navegador nao chega
ao servidor). Compara o dia publicado com a foto anterior em docs/historico.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests


def montar_mensagem(dados: dict, anterior: dict | None, tickers: list[str], queda_minima: int = 10) -> str:
    linhas = []
    antes = {t: (f, lp) for t, f, lp in (anterior or {}).get("acoes", [])}
    agora = {a["t"]: a for a in dados.get("acoes") or []}
    for t in tickers:
        a = agora.get(t)
        if not a:
            continue
        nota = round(a["final"])
        if t in antes and antes[t][0] is not None and abs(nota - antes[t][0]) >= queda_minima:
            linhas.append(f"{t}: nota {antes[t][0]} -> {nota}")
        for al in a.get("al") or []:
            if "Prejuízo" in al or "negativo" in al:
                linhas.append(f"{t}: {al}")
    dia = dados.get("dataCotacao") or ""
    for f in dados.get("fatos") or []:
        if f["t"] in tickers and f["data"] >= (anterior or {}).get("data", dia) and f["cat"].lower() == "fato relevante":
            linhas.append(f"{f['t']}: fato relevante em {f['data']}: {f['assunto'][:120]}")
    erros = [c for c in dados.get("coleta") or [] if c.get("situacao") == "erro"]
    if erros:
        linhas.append("Coleta com erro: " + ", ".join(c["fonte"] for c in erros))
    if not linhas:
        return ""
    return "Radar de Investimentos, cotações de " + dia + "\n" + "\n".join("• " + x for x in linhas)


def enviar(cfg: dict, pasta_docs: Path) -> str:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "sem TELEGRAM_TOKEN/TELEGRAM_CHAT_ID: alertas desligados"
    dados = json.loads((pasta_docs / "dados.json").read_text(encoding="utf-8"))
    hist = sorted((pasta_docs / "historico").glob("????-??-??.json"))
    anteriores = [h for h in hist if h.stem < (dados.get("dataCotacao") or "")]
    anterior = json.loads(anteriores[-1].read_text(encoding="utf-8")) if anteriores else None
    tickers = [str(t).upper() for t in ((cfg.get("alertas") or {}).get("tickers") or [])]
    texto = montar_mensagem(dados, anterior, tickers)
    if not texto:
        return "nada novo para avisar"
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": texto[:4000]}, timeout=30)
    return f"Telegram respondeu HTTP {r.status_code}"

"""Etapa 4: resumo em texto de cada empresa, escrito pelo Claude a partir dos numeros do painel.

So roda quando a variavel ANTHROPIC_API_KEY existe (no GitHub: Settings > Secrets > Actions).
Cada resumo fica guardado com a "impressao digital" dos dados que o geraram: no dia seguinte, so
as empresas cujos numeros mudaram pedem um texto novo. Sem chave, o painel segue sem resumos.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

MODELO = "claude-opus-5-5"
QUANTOS = 40          # maiores notas finais e de longo prazo que recebem resumo

INSTRUCOES = """Você escreve o resumo de uma empresa listada na B3 para um painel de triagem de ações.
Use só os números recebidos. Em 3 frases curtas, em português do Brasil e sem jargão: o que os números
mostram de bom, o que pede atenção e o que falta saber. Não recomende compra nem venda, não dê preço-alvo
e não faça previsões. Não invente fatos sobre a empresa que não estejam nos dados."""


def _campos(a: dict) -> dict:
    """O que vai para o modelo: so numeros do painel, arredondados para o texto nao mudar a toa."""
    r = lambda v, c=2: None if v is None else round(v, c)  # noqa: E731
    return {"codigo": a["t"], "empresa": a.get("razao") or a.get("n"), "setor": a.get("s"), "preco": a.get("p"),
            "nota_final": r(a.get("final"), 0), "nota_longo_prazo": a.get("lp"), "notas_por_metodo": a.get("m"),
            "dividendos_12m": r(a.get("dy"), 3), "dividendo_tipico": r(a.get("dyTipico"), 3), "preco_lucro": r(a.get("pl"), 1),
            "preco_patrimonio": r(a.get("pvp"), 1), "retorno_patrimonio": r(a.get("roe"), 3), "divida_liquida_por_geracao": r(a.get("divEbit"), 1),
            "variacao_12m": r(a.get("var12"), 2), "alertas": a.get("al") or [],
            "checklist_longo_prazo": [[t, ok] for t, ok in (a.get("lpCheck") or [])]}


def escolher(acoes: list) -> list:
    por_final = sorted(acoes, key=lambda a: -(a.get("final") or 0))[:QUANTOS // 2]
    por_lp = sorted([a for a in acoes if a.get("lp") is not None], key=lambda a: (-a["lp"], -(a.get("final") or 0)))[:QUANTOS // 2]
    vistos, saida = set(), []
    for a in por_final + por_lp:
        if a["t"] not in vistos:
            vistos.add(a["t"])
            saida.append(a)
    return saida


def resumir(cliente, dados: dict) -> str | None:
    resposta = cliente.beta.messages.create(
        model=MODELO,
        max_tokens=2000,
        system=INSTRUCOES,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",          # se o modelo recusar, a API refaz no modelo de reserva
        messages=[{"role": "user", "content": json.dumps(dados, ensure_ascii=False)}],
    )
    if resposta.stop_reason == "refusal":
        return None
    texto = "".join(b.text for b in resposta.content if b.type == "text").strip()
    return texto or None


def aplicar(dados: dict, pasta_cache: Path, cliente=None) -> int:
    """Coloca o campo `resumo` nas acoes escolhidas. Devolve quantos textos novos foram pedidos."""
    if cliente is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return 0
        import anthropic
        cliente = anthropic.Anthropic()
    arquivo = Path(pasta_cache) / "resumos.json"
    try:
        guardados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        guardados = {}
    novos = 0
    for a in escolher(dados.get("acoes") or []):
        entrada = _campos(a)
        digital = hashlib.sha256(json.dumps(entrada, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        salvo = guardados.get(a["t"])
        if not salvo or salvo.get("digital") != digital:
            try:
                texto = resumir(cliente, entrada)
            except Exception as e:  # falha de um resumo nao pode derrubar a exportacao
                print(f"  resumo de {a['t']} falhou: {type(e).__name__}: {str(e)[:120]}")
                continue
            if not texto:
                continue
            salvo = guardados[a["t"]] = {"digital": digital, "texto": texto, "em": date.today().isoformat()}
            novos += 1
        a["resumo"], a["resumoEm"] = salvo["texto"], salvo["em"]
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(guardados, ensure_ascii=False), encoding="utf-8")
    return novos

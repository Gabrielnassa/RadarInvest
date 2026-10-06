"""Leitura do config.yaml e do arquivo .env."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def carregar_env(caminho: Path | None = None) -> None:
    """Carrega pares CHAVE=valor do .env sem sobrescrever o que ja esta no ambiente."""
    caminho = caminho or RAIZ / ".env"
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave, valor = chave.strip(), valor.strip().strip('"').strip("'")
        if chave and valor and chave not in os.environ:
            os.environ[chave] = valor


def carregar(caminho: Path | None = None) -> dict:
    caminho = Path(caminho) if caminho else RAIZ / "config.yaml"
    with open(caminho, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    base = caminho.resolve().parent
    cfg["_banco"] = _resolver(base, cfg.get("banco", "dados/radar.db"))
    cfg["_cache"] = _resolver(base, cfg.get("pasta_cache", "dados/cache"))
    return cfg


def _resolver(base: Path, valor: str) -> Path:
    p = Path(valor)
    return p if p.is_absolute() else base / p

"""Acesso a rede: novas tentativas, espera entre chamadas e download com cache."""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

UA_PADRAO = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarInvestimentos/0.1"


class ErroRede(Exception):
    """Falha de rede depois de esgotar as tentativas."""


class Rede:
    def __init__(self, cfg: dict | None = None, dormir=time.sleep):
        cfg = cfg or {}
        self.timeout = cfg.get("timeout_segundos", 90)
        self.tentativas = max(1, int(cfg.get("tentativas", 4)))
        self.dormir = dormir
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": cfg.get("user_agent", UA_PADRAO), "Accept": "*/*"})

    def get(self, url, params=None, headers=None, stream=False, aceitar=(200,), verificar=True):
        """GET com novas tentativas em erro de conexao, HTTP 429 e HTTP 5xx."""
        ultimo = "sem resposta"
        if not verificar:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        for i in range(self.tentativas):
            espera = None
            try:
                r = self.sessao.get(url, params=params, headers=headers, timeout=self.timeout,
                                    stream=stream, verify=verificar)
            except requests.RequestException as e:
                ultimo = motivo(e)
                if isinstance(e, requests.exceptions.SSLError):
                    break  # certificado nao melhora tentando de novo
            else:
                if r.status_code in aceitar:
                    return r
                codigo = r.status_code
                espera = _retry_after(r)
                r.close()
                if codigo != 429 and codigo < 500:
                    raise ErroRede(f"{_curta(url)} respondeu HTTP {codigo}")
                ultimo = f"HTTP {codigo}"
            if i < self.tentativas - 1:
                self.dormir(espera if espera is not None else min(60, 2 ** (i + 1)))
        raise ErroRede(f"{_curta(url)}: {ultimo}")

    def json(self, url, params=None, headers=None, aceitar=(200,)):
        r = self.get(url, params=params, headers=headers, aceitar=aceitar)
        try:
            return r.status_code, r.json()
        except ValueError as e:
            raise ErroRede(f"{_curta(url)} respondeu HTTP {r.status_code} sem JSON") from e

    def baixar(self, url, destino: Path, max_idade_horas: float | None = None,
               verificar: bool = True) -> Path | None:
        """Baixa para `destino`. Reaproveita o arquivo se for mais novo que `max_idade_horas`
        (None = reaproveita sempre). Devolve None se o servidor responder 404."""
        destino = Path(destino)
        if destino.exists() and destino.stat().st_size > 0:
            if max_idade_horas is None:
                return destino
            idade = (time.time() - destino.stat().st_mtime) / 3600
            if idade < max_idade_horas:
                return destino
        destino.parent.mkdir(parents=True, exist_ok=True)
        r = self.get(url, stream=True, aceitar=(200, 404), verificar=verificar)
        if r.status_code == 404:
            r.close()
            return None
        parcial = destino.with_suffix(destino.suffix + ".part")
        try:
            with open(parcial, "wb") as f:
                for bloco in r.iter_content(chunk_size=1 << 20):
                    if bloco:
                        f.write(bloco)
        except requests.RequestException as e:
            parcial.unlink(missing_ok=True)
            raise ErroRede(f"{_curta(url)}: download interrompido ({motivo(e)})") from e
        finally:
            r.close()
        parcial.replace(destino)
        return destino

    def testar(self, url, params=None, headers=None, verificar=True) -> tuple[bool, str]:
        """Uma unica tentativa, para o diagnostico."""
        try:
            r = self.sessao.get(url, params=params, headers=headers, timeout=min(self.timeout, 30),
                                stream=True, verify=verificar)
        except requests.RequestException as e:
            return False, motivo(e)
        try:
            inicio = next(r.iter_content(chunk_size=256), b"")
        except requests.RequestException as e:
            return False, motivo(e)
        finally:
            r.close()
        ok = r.status_code == 200 and len(inicio) > 0
        return ok, f"HTTP {r.status_code}, {r.headers.get('Content-Type', '?')}"


def _retry_after(r) -> float | None:
    valor = r.headers.get("Retry-After")
    if not valor:
        return None
    try:
        return max(1.0, min(120.0, float(valor)))
    except ValueError:
        return None


def _curta(url: str) -> str:
    partes = urlsplit(url)
    return f"{partes.netloc}{partes.path}"


def motivo(e: Exception) -> str:
    """Explica em poucas palavras por que a chamada falhou."""
    ex = requests.exceptions
    if isinstance(e, ex.SSLError):
        return "erro de certificado (SSL)"
    if isinstance(e, ex.ProxyError):
        return "bloqueado pelo proxy ou firewall da rede"
    if isinstance(e, ex.Timeout):
        return "tempo esgotado sem resposta"
    if isinstance(e, ex.ConnectionError):
        return "sem conexao com o servidor"
    return f"{type(e).__name__}: {str(e)[:120]}"

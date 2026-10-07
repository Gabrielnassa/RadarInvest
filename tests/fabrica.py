"""Monta arquivos de exemplo no mesmo formato das fontes reais, para os testes."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _p(centavos: int) -> str:
    return str(centavos).zfill(13)


def linha_cotahist(data="20261002", codbdi="02", ticker="PETR4", tpmerc="010", nome="PETROBRAS",
                   especi="PN      N2", abe=3800, maxi=3850, mini=3790, med=3820, ult=3810,
                   neg=12345, qtd=1000000, vol=3810000000, fator=1, isin="BRPETRACNPR6") -> str:
    s = ("01" + data + codbdi + ticker.ljust(12) + tpmerc + nome.ljust(12) + especi.ljust(10)
         + "   " + "R$  " + _p(abe) + _p(maxi) + _p(mini) + _p(med) + _p(ult) + _p(0) + _p(0)
         + str(neg).zfill(5) + str(qtd).zfill(18) + str(vol).zfill(18) + _p(0) + "0" + "99991231"
         + str(fator).zfill(7) + "0" * 13 + isin.ljust(12) + "100")
    assert len(s) == 245, len(s)
    return s


def zip_cotahist(destino: Path, linhas: list[str], ano=2026) -> Path:
    cab = ("00" + f"COTAHIST.{ano}" + "BOVESPA " + f"{ano}1002").ljust(245)
    rod = ("99" + f"COTAHIST.{ano}" + "BOVESPA " + f"{ano}1002" + str(len(linhas) + 2).zfill(11)).ljust(245)
    texto = "\r\n".join([cab, *linhas, rod]) + "\r\n"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"COTAHIST_A{ano}.TXT", texto.encode("latin-1"))
    return destino


def _csv(cabecalho: list[str], linhas: list[list]) -> bytes:
    buf = io.StringIO()
    buf.write(";".join(cabecalho) + "\r\n")
    for linha in linhas:
        buf.write(";".join(str(c) for c in linha) + "\r\n")
    return buf.getvalue().encode("latin-1")


CAB_FCA_GERAL = ["CNPJ_Companhia", "Data_Referencia", "Versao", "ID_Documento", "Nome_Empresarial",
                 "Codigo_CVM", "Situacao_Registro_CVM", "Setor_Atividade"]
CAB_FCA_VM = ["CNPJ_Companhia", "Data_Referencia", "Versao", "ID_Documento", "Nome_Empresarial",
              "Valor_Mobiliario", "Codigo_Negociacao", "Mercado", "Data_Inicio_Negociacao",
              "Data_Fim_Negociacao", "Segmento"]


def zip_fca(destino: Path, ano: int, geral: list[list], valores: list[list]) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"fca_cia_aberta_geral_{ano}.csv", _csv(CAB_FCA_GERAL, geral))
        zf.writestr(f"fca_cia_aberta_valor_mobiliario_{ano}.csv", _csv(CAB_FCA_VM, valores))
    return destino


CAB_FLUXO = ["CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP", "MOEDA", "ESCALA_MOEDA",
             "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC", "CD_CONTA", "DS_CONTA", "VL_CONTA", "ST_CONTA_FIXA"]
CAB_SALDO = [c for c in CAB_FLUXO if c != "DT_INI_EXERC"]


def linha_conta(cnpj, dt_refer, conta, descricao, valor, ordem="ÚLTIMO", versao=1, escala="MIL",
                dt_ini=None, nome="EMPRESA", cd_cvm="9512") -> list:
    """Linha de demonstrativo. Com dt_ini vira linha de DRE/DFC; sem, de balanco."""
    base = [cnpj, dt_refer, versao, nome, cd_cvm, "DF", "REAL", escala, ordem]
    if dt_ini is not None:
        base.append(dt_ini)
    return base + [dt_refer, conta, descricao, f"{valor:.10f}", "S"]


def zip_demonstrativos(destino: Path, origem: str, ano: int, arquivos: dict[str, list[list]]) -> Path:
    """arquivos: {'DRE_con': [linhas], 'BPA_ind': [linhas], ...}"""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{origem}_cia_aberta_{ano}.csv", b"CNPJ_CIA;DT_REFER\r\n")
        for chave, linhas in arquivos.items():
            cab = CAB_SALDO if chave.startswith(("BPA", "BPP")) else CAB_FLUXO
            zf.writestr(f"{origem}_cia_aberta_{chave}_{ano}.csv", _csv(cab, linhas))
    return destino


def _ts(dia: str) -> int:
    return int(datetime.fromisoformat(dia + "T13:00:00").replace(tzinfo=timezone.utc).timestamp())


def json_yahoo(dividendos: dict[str, float]) -> dict:
    eventos = {str(_ts(d)): {"amount": v, "date": _ts(d)} for d, v in dividendos.items()}
    resultado = {"meta": {"currency": "BRL", "symbol": "PETR4.SA"}, "timestamp": [], "indicators": {}}
    if eventos:
        resultado["events"] = {"dividends": eventos}
    return {"chart": {"result": [resultado], "error": None}}


def json_mercados(itens: list[tuple]) -> list[dict]:
    """itens: (id, simbolo, nome, valor_de_mercado, preco)"""
    return [{"id": i, "symbol": s, "name": n, "current_price": p, "market_cap": cap, "market_cap_rank": pos,
             "total_volume": cap / 20, "ath": p * 2, "ath_change_percentage": -50.0,
             "ath_date": "2025-10-06T18:57:42.558Z", "circulating_supply": cap / p,
             "last_updated": "2026-10-06T12:00:00.000Z"}
            for pos, (i, s, n, cap, p) in enumerate(itens, start=1)]


def json_historico(dias: dict[str, float]) -> dict:
    ms = lambda d: _ts(d) * 1000  # noqa: E731
    return {"prices": [[ms(d), p] for d, p in dias.items()],
            "market_caps": [[ms(d), p * 1e6] for d, p in dias.items()],
            "total_volumes": [[ms(d), p * 1e4] for d, p in dias.items()]}


def gravar_json(destino: Path, dado) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dado), encoding="utf-8")
    return destino


CAB_FII_GERAL = ["CNPJ_Fundo_Classe", "Data_Referencia", "Versao", "Nome_Fundo_Classe", "Codigo_ISIN",
                 "Segmento_Atuacao", "Mandato"]
CAB_FII_COMP = ["CNPJ_Fundo_Classe", "Data_Referencia", "Versao", "Total_Numero_Cotistas", "Valor_Ativo",
                "Patrimonio_Liquido", "Cotas_Emitidas", "Valor_Patrimonial_Cotas", "Percentual_Dividend_Yield_Mes"]


def zip_fii(destino: Path, ano: int, geral: list[list], complemento: list[list]) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"inf_mensal_fii_geral_{ano}.csv", _csv(CAB_FII_GERAL, geral))
        zf.writestr(f"inf_mensal_fii_complemento_{ano}.csv", _csv(CAB_FII_COMP, complemento))
        zf.writestr(f"inf_mensal_fii_ativo_passivo_{ano}.csv", b"CNPJ_Fundo_Classe;Data_Referencia\r\n")
    return destino


CAB_TESOURO = ["Tipo Titulo", "Data Vencimento", "Data Base", "Taxa Compra Manha", "Taxa Venda Manha",
               "PU Compra Manha", "PU Venda Manha", "PU Base Manha"]


def csv_tesouro(destino: Path, linhas: list[list]) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(_csv(CAB_TESOURO, linhas))
    return destino

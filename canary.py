#!/usr/bin/env python3
"""Canary readonly de cotacoes Solana/Jupiter.

Este programa faz exatamente uma chamada JSON-RPC getTokenSupply e duas
chamadas de cotacao Jupiter. Ele nao cria carteira, transacao ou assinatura.
A cotacao nao prova que uma venda pode ser executada. Poeira, slippage e taxas
de rede nao sao contabilizados.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
DEFAULT_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
RPC_URL = os.environ.get("SOLANA_RPC_URL", DEFAULT_RPC_URL)
QUOTE_URL = os.environ.get("JUPITER_QUOTE_URL", DEFAULT_QUOTE_URL)

WSOL_MINT = "So11111111111111111111111111111111111111112"
SLIPPAGE_BPS = 50
RPC_TIMEOUT_SECONDS = 20
QUOTE_TIMEOUT_SECONDS = 20
MIN_SOL_OUT = Decimal("0.000000001")
DECIMAL_PRECISION = 1100
LAMPORTS_PER_SOL = 1_000_000_000

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {character: index for index, character in enumerate(BASE58_ALPHABET)}


class CanaryError(Exception):
    """Erro interno com texto seguro para exibir ao usuario."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class ValidationError(CanaryError):
    pass


class ConfigurationError(CanaryError):
    pass


class InconclusiveError(CanaryError):
    pass


class NotTokenMintError(InconclusiveError):
    pass


class HTTPStatusFailure(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class TransportFailure(Exception):
    pass


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes


@dataclass(frozen=True)
class Supply:
    decimals: int


@dataclass(frozen=True)
class Quote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    route_plan: tuple[Any, ...]


@dataclass(frozen=True)
class Evaluation:
    outcome: str
    sell_sol: Optional[Decimal]
    buy_price_sol_per_token: Optional[Decimal]
    difference_pct: Optional[Decimal]
    reason: Optional[str] = None

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome == "positive" else 1


Fetcher = Callable[[str, bytes, Mapping[str, str], int], HTTPResponse]
Evaluator = Callable[[int, Quote, Quote], Evaluation]


def utc_now_text() -> str:
    """Retorna um horario UTC sem acentos e sem dependencias externas."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def decode_base58(value: str) -> bytes:
    """Decodifica base58 Bitcoin/Solana sem bibliotecas externas."""
    if not isinstance(value, str) or not value:
        raise ValidationError("endereco invalido: use base58 com exatamente 32 bytes.")
    if not 32 <= len(value) <= 44:
        raise ValidationError("endereco invalido: use base58 com exatamente 32 bytes.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValidationError("endereco invalido: use base58 com exatamente 32 bytes.") from exc

    number = 0
    for character in value:
        digit = BASE58_INDEX.get(character)
        if digit is None:
            raise ValidationError("endereco invalido: use base58 com exatamente 32 bytes.")
        number = number * 58 + digit

    decoded = b""
    if number:
        decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_zeroes = len(value) - len(value.lstrip("1"))
    decoded = b"\x00" * leading_zeroes + decoded
    if len(decoded) != 32:
        raise ValidationError("endereco invalido: use base58 com exatamente 32 bytes.")
    return decoded


def validate_mint(value: str) -> str:
    decoded = decode_base58(value)
    wsol_bytes = decode_base58(WSOL_MINT)
    if decoded == wsol_bytes:
        raise ValidationError("mint invalida: WSOL nao pode ser usado como o token do par.")
    return value


def validate_https_url(value: str, label: str) -> str:
    """Valida configuracao HTTPS sem incluir a URL em mensagens de erro."""
    if not isinstance(value, str) or not value:
        raise ConfigurationError(label + " invalida: use um endpoint HTTPS.")
    if any(ord(character) < 0x20 for character in value):
        raise ConfigurationError(label + " invalida: use um endpoint HTTPS.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise ConfigurationError(label + " invalida: use um endpoint HTTPS.")
    if parsed.scheme.lower() != "https" or not hostname:
        raise ConfigurationError(label + " invalida: use um endpoint HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError(label + " invalida: credenciais devem ser enviadas por ambiente.")
    if port is not None and not (1 <= port <= 65535):
        raise ConfigurationError(label + " invalida: porta HTTPS invalida.")
    if parsed.fragment:
        raise ConfigurationError(label + " invalida: fragmentos nao sao aceitos.")
    return value


def _unsigned_integer_string(value: Any, field_name: str, allow_zero: bool = True) -> int:
    if not isinstance(value, str) or not value or not value.isascii():
        raise InconclusiveError("resposta da Jupiter com schema invalido.")
    if not value.isdigit():
        raise InconclusiveError("resposta da Jupiter com schema invalido.")
    if len(value) > 1 and value.startswith("0"):
        raise InconclusiveError("resposta da Jupiter com schema invalido.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise InconclusiveError("resposta da Jupiter com schema invalido.")
    if not allow_zero and number <= 0:
        raise InconclusiveError("resposta da Jupiter sem saida positiva.")
    return number


def _response_parts(response: Any) -> HTTPResponse:
    if isinstance(response, HTTPResponse):
        return response
    if isinstance(response, tuple) and len(response) == 2:
        status, body = response
        if isinstance(status, bool) or not isinstance(status, int):
            raise TransportFailure()
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, bytes):
            raise TransportFailure()
        return HTTPResponse(status=status, body=body)
    raise TransportFailure()


def urllib_fetcher(
    url: str,
    data: bytes,
    headers: Mapping[str, str],
    timeout: int,
) -> HTTPResponse:
    """Fetcher HTTP injetavel e sem qualquer log de URL ou excecao."""
    method = "POST" if data else "GET"
    request = Request(url, data=data or None, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.getcode())
            body = response.read()
            return HTTPResponse(status=status, body=body)
    except HTTPError as exc:
        raise HTTPStatusFailure(int(exc.code)) from None
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise TransportFailure() from exc


def _safe_http_reason(service: str, status: int) -> str:
    if service == "RPC Solana" and status == 403:
        return "falha HTTP 403 no RPC Solana; verifique permissoes do endpoint."
    if service == "Jupiter" and status in (401, 403):
        return "falha HTTP 401/403 na Jupiter; verifique JUPITER_API_KEY e permissoes."
    if status == 429:
        return "falha HTTP 429; limite de requisicoes atingido (rate limiting)."
    if 500 <= status <= 599:
        return "falha HTTP " + str(status) + "; servidor transitorio."
    return "falha HTTP " + str(status) + " no servico " + service + "."


def _fetch_json(
    service: str,
    url: str,
    data: bytes,
    headers: Mapping[str, str],
    timeout: int,
    fetcher: Fetcher,
) -> Any:
    try:
        response = _response_parts(fetcher(url, data, headers, timeout))
    except HTTPStatusFailure as exc:
        raise InconclusiveError(_safe_http_reason(service, exc.status))
    except HTTPError as exc:
        raise InconclusiveError(_safe_http_reason(service, int(exc.code))) from None
    except (TransportFailure, URLError, TimeoutError, socket.timeout, OSError):
        raise InconclusiveError("falha de rede no servico " + service + ".") from None
    except CanaryError:
        raise
    except Exception:
        raise InconclusiveError("falha de rede no servico " + service + ".") from None

    if response.status < 200 or response.status > 299:
        raise InconclusiveError(_safe_http_reason(service, response.status))
    try:
        text = response.body.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise InconclusiveError("resposta invalida do servico " + service + ".") from None


def _validate_jsonrpc_response(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise InconclusiveError("resposta invalida do RPC Solana.")
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
        raise InconclusiveError("resposta invalida do RPC Solana.")
    if "error" in payload:
        error = payload["error"]
        if isinstance(error, dict) and error.get("code") == -32602:
            detail = str(error.get("message", "")).lower()
            if "not a token mint" in detail:
                raise NotTokenMintError("isso nao parece um token mint: getTokenSupply recusou a conta.")
        raise InconclusiveError("RPC Solana retornou erro.")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise InconclusiveError("resposta invalida do RPC Solana.")
    value = result.get("value")
    if not isinstance(value, dict):
        raise InconclusiveError("resposta invalida do RPC Solana.")
    amount = value.get("amount")
    if _unsigned_integer_string_rpc(amount) is None:
        raise InconclusiveError("resposta invalida do RPC Solana.")
    decimals = value.get("decimals")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 255:
        raise NotTokenMintError("isso nao parece um token mint: decimals invalido no RPC Solana.")
    return value


def _unsigned_integer_string_rpc(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdigit():
        return None
    if len(value) > 1 and value.startswith("0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_token_supply(
    mint: str,
    fetcher: Fetcher = urllib_fetcher,
    rpc_url: Optional[str] = None,
) -> Supply:
    endpoint = RPC_URL if rpc_url is None else rpc_url
    validate_https_url(endpoint, "SOLANA_RPC_URL")
    request_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenSupply",
        "params": [mint, {"commitment": "confirmed"}],
    }
    data = json.dumps(request_body, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    payload = _fetch_json(
        "RPC Solana",
        endpoint,
        data,
        {"Accept": "application/json", "Content-Type": "application/json"},
        RPC_TIMEOUT_SECONDS,
        fetcher,
    )
    value = _validate_jsonrpc_response(payload)
    decimals = value["decimals"]
    return Supply(decimals=decimals)


def _quote_url(
    input_mint: str,
    output_mint: str,
    amount: int,
    quote_url: Optional[str] = None,
) -> str:
    endpoint = QUOTE_URL if quote_url is None else quote_url
    validate_https_url(endpoint, "JUPITER_QUOTE_URL")
    parsed = urlsplit(endpoint)
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(SLIPPAGE_BPS),
    }
    existing = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in params
    ]
    query = urlencode(existing + list(params.items()))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _validate_quote_response(
    payload: Any,
    input_mint: str,
    output_mint: str,
    amount: int,
) -> Quote:
    if not isinstance(payload, dict):
        raise InconclusiveError("resposta da Jupiter com schema invalido.")
    if payload.get("inputMint") != input_mint or payload.get("outputMint") != output_mint:
        raise InconclusiveError("resposta da Jupiter com par de mints diferente do pedido.")
    route_plan = payload.get("routePlan")
    if not isinstance(route_plan, list) or not route_plan:
        raise InconclusiveError("Jupiter nao retornou rota.")
    if payload.get("inAmount") != str(amount):
        raise InconclusiveError("resposta da Jupiter com inAmount diferente do pedido.")
    if "swapMode" in payload and payload.get("swapMode") != "ExactIn":
        raise InconclusiveError("resposta da Jupiter com swapMode inesperado.")
    out_amount = _unsigned_integer_string(payload.get("outAmount"), "outAmount", allow_zero=output_mint == WSOL_MINT)
    return Quote(
        input_mint=input_mint,
        output_mint=output_mint,
        in_amount=amount,
        out_amount=out_amount,
        route_plan=tuple(route_plan),
    )


def fetch_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    fetcher: Fetcher = urllib_fetcher,
    quote_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Quote:
    endpoint = QUOTE_URL if quote_url is None else quote_url
    url = _quote_url(input_mint, output_mint, amount, endpoint)
    if api_key is None:
        api_key = os.environ.get("JUPITER_API_KEY")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    payload = _fetch_json(
        "Jupiter",
        url,
        b"",
        headers,
        QUOTE_TIMEOUT_SECONDS,
        fetcher,
    )
    return _validate_quote_response(payload, input_mint, output_mint, amount)


def _quote_field(quote: Any, field: str) -> Any:
    if isinstance(quote, Quote):
        return getattr(quote, field)
    if isinstance(quote, Mapping):
        return quote.get(field)
    return None


def evaluate(decimals: int, sell_quote: Quote, buy_quote: Quote) -> Evaluation:
    """Avalia cotacoes com precision Decimal >= 1000, sem float.

    O denominador da diferenca e o preco de compra, conforme definido pelo
    usuario. Saida de ate um lamport ou queda superior a 60% contra a compra
    gera suspeita, nao prova de honeypot. Taxas de rede nao sao contabilizadas.
    """
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 255:
        return Evaluation("inconclusive", None, None, None, "decimals invalido.")

    sell_input = _quote_field(sell_quote, "input_mint")
    sell_output = _quote_field(sell_quote, "output_mint")
    buy_input = _quote_field(buy_quote, "input_mint")
    buy_output = _quote_field(buy_quote, "output_mint")
    sell_amount = _quote_field(sell_quote, "in_amount")
    buy_amount = _quote_field(buy_quote, "in_amount")
    sell_out = _quote_field(sell_quote, "out_amount")
    buy_out = _quote_field(buy_quote, "out_amount")
    sell_route = _quote_field(sell_quote, "route_plan")
    buy_route = _quote_field(buy_quote, "route_plan")

    if (
        sell_output != WSOL_MINT
        or buy_input != WSOL_MINT
        or sell_input != buy_output
        or not isinstance(sell_route, (list, tuple))
        or not sell_route
        or not isinstance(buy_route, (list, tuple))
        or not buy_route
        or sell_amount != 10**decimals
        or buy_amount != LAMPORTS_PER_SOL
        or isinstance(sell_out, bool)
        or not isinstance(sell_out, int)
        or isinstance(buy_out, bool)
        or not isinstance(buy_out, int)
        or sell_out < 0
        or buy_out <= 0
    ):
        return Evaluation("inconclusive", None, None, None, "rotas bidirecionais ou valores incoerentes.")

    with localcontext() as context:
        context.prec = max(DECIMAL_PRECISION, 1000)
        sell_sol = Decimal(sell_out) / Decimal(LAMPORTS_PER_SOL)
        token_amount = Decimal(buy_out) / Decimal(10**decimals)
        if token_amount <= 0:
            return Evaluation("inconclusive", sell_sol, None, None, "saida de compra invalida.")
        buy_sol = Decimal(buy_amount) / Decimal(LAMPORTS_PER_SOL)
        buy_price = buy_sol / token_amount
        difference = (abs(buy_price - sell_sol) / buy_price) * Decimal(100)
        if sell_sol <= MIN_SOL_OUT:
            return Evaluation("suspected", sell_sol, buy_price, difference, "cotacao com rota, mas saida nula ou irrisoria (ate 1 lamport).")
        if sell_sol < buy_price and difference > Decimal(60):
            return Evaluation("suspected", sell_sol, buy_price, difference, "preco de venda colapsado em mais de 60% contra a compra.")
        if difference <= Decimal(60):
            return Evaluation("positive", sell_sol, buy_price, difference)
        return Evaluation("alert", sell_sol, buy_price, difference)


def _decimal_text(value: Optional[Decimal]) -> str:
    if value is None or not value.is_finite():
        return "indisponivel"
    with localcontext() as context:
        context.prec = 18
        display_value = +value
    if display_value.adjusted() < -18 or display_value.adjusted() > 18:
        mantissa, exponent = format(display_value, "E").split("E")
        if "." in mantissa:
            mantissa = mantissa.rstrip("0").rstrip(".")
        text = mantissa + "E" + exponent
    else:
        text = format(display_value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def render_evaluation(result: Evaluation) -> None:
    if result.outcome == "positive":
        print("TOKEN DEIXA VENDER (COTACAO): rota de venda disponivel na Jupiter.")
        print("Venda de 1 token: " + _decimal_text(result.sell_sol) + " SOL.")
        print("Diferenca: " + _decimal_text(result.difference_pct) + "%.")
        print("Uma cotacao nao executa nem garante a venda; nao confirma ausencia de honeypot.")
        print("Poeira, slippage e taxas de rede nao foram contabilizados.")
        return
    if result.outcome == "suspected":
        print("SUSPEITA DE HONEYPOT: " + (result.reason or "saida de venda anormal."))
        print("Venda de 1 token: " + _decimal_text(result.sell_sol) + " SOL.")
        print("Diferenca: " + _decimal_text(result.difference_pct) + "%.")
        print("ALERTA: taxa disfarcada / saida cara. Nao compre com base neste resultado.")
        print("Nao e confirmacao de honeypot: liquidez, arredondamento, impacto ou falha de cotacao tambem podem explicar o sinal.")
        return
    if result.outcome == "alert":
        print("ALERTA: taxa disfarcada / saida cara (diferenca acima de 60%; nao prova de taxa).")
        print("Diferenca: " + _decimal_text(result.difference_pct) + "%.")
        print("Nao ha garantia positiva de venda; resultado incoerente/nao aprovado.")
        print("Poeira, slippage e taxas de rede nao foram contabilizados.")
        return
    reason = result.reason or "cotacoes incoerentes."
    print(
        "TESTE INCONCLUSIVO - tente depois ou use outro RPC. Motivo: "
        + reason
        + " Nao compre com base neste resultado. Falha ou ausencia de rota nao comprova honeypot."
    )


def run_canary(
    mint: str,
    fetcher: Fetcher = urllib_fetcher,
    evaluator: Evaluator = evaluate,
) -> Evaluation:
    validate_mint(mint)
    validate_https_url(RPC_URL, "SOLANA_RPC_URL")
    validate_https_url(QUOTE_URL, "JUPITER_QUOTE_URL")

    supply = fetch_token_supply(mint, fetcher=fetcher, rpc_url=RPC_URL)
    sell_amount = 10**supply.decimals
    sell_quote = fetch_quote(
        mint,
        WSOL_MINT,
        sell_amount,
        fetcher=fetcher,
        quote_url=QUOTE_URL,
    )
    buy_quote = fetch_quote(
        WSOL_MINT,
        mint,
        LAMPORTS_PER_SOL,
        fetcher=fetcher,
        quote_url=QUOTE_URL,
    )
    try:
        result = evaluator(supply.decimals, sell_quote, buy_quote)
    except CanaryError:
        raise
    except Exception:
        raise InconclusiveError("falha ao avaliar cotacoes.") from None
    if not isinstance(result, Evaluation):
        raise InconclusiveError("avaliacao com schema invalido.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canary.py",
        description="Teste readonly de cotacao de venda antes da compra.",
    )
    parser.add_argument("mint", help="mint do token em base58 Solana")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    fetcher: Fetcher = urllib_fetcher,
    evaluator: Evaluator = evaluate,
) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1

    print("UTC: " + utc_now_text())
    try:
        result = run_canary(args.mint, fetcher=fetcher, evaluator=evaluator)
    except ValidationError as exc:
        print("ERRO DE VALIDACAO: " + exc.safe_message)
        return 1
    except NotTokenMintError as exc:
        print("ERRO: " + exc.safe_message)
        return 1
    except (ConfigurationError, InconclusiveError) as exc:
        print(
            "TESTE INCONCLUSIVO - tente depois ou use outro RPC. Motivo: "
            + exc.safe_message
            + " Nao compre com base neste resultado. Falha ou ausencia de rota nao comprova honeypot."
        )
        return 1
    except KeyboardInterrupt:
        print(
            "TESTE INCONCLUSIVO - tente depois ou use outro RPC. Operacao interrompida. Nao compre com base neste resultado. "
            "Falha ou ausencia de rota nao comprova honeypot."
        )
        return 1
    render_evaluation(result)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

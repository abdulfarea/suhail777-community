#!/usr/bin/env python3
"""Defesa readonly de tokens Solana, sem carteira, assinatura ou transacao."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import socket
import sys
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError

import canary


DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
RPC_TIMEOUT_SECONDS = 20
LEGACY_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ALLOWED_RPC_METHODS = frozenset(
    ("getAccountInfo", "getTokenLargestAccounts", "getTokenAccountsByOwner", "getBalance")
)
SIGNAL_ORDER = (
    "MINT_AUTHORITY",
    "FREEZE_AUTHORITY",
    "TOP10_70",
    "CANARY_EXIT",
    "EXTENSIONS_PENDING",
    "DATA_UNAVAILABLE",
)
FILTER_CAVEAT = "Nao altera o feed Pump e nao garante ausencia de golpes."
POSITIONS_CAVEAT = (
    "Posicoes sao um snapshot de holdings, nao ordens; a lista nao e enriquecida automaticamente "
    "e nao esconde moedas da carteira; pode conter spam."
)
SAMPLE_CAVEAT = "A contagem de getTokenLargestAccounts e uma amostra, nao total de holders."


class DefenseError(Exception):
    """Erro seguro para a interface, sem URL, credencial ou excecao crua."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.safe_message = message


class ValidationError(DefenseError):
    pass


class ConfigurationError(DefenseError):
    pass


class InconclusiveError(DefenseError):
    pass


class HTTPStatusFailure(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


Fetcher = Callable[[str, bytes, Mapping[str, str], int], Any]
QuoteChecker = Callable[..., Any]


def utc_now_text() -> str:
    return canary.utc_now_text()


def _rpc_url(value: Optional[str] = None) -> str:
    endpoint = value if value is not None else os.environ.get("SOLANA_RPC_URL", DEFAULT_RPC_URL)
    try:
        return canary.validate_https_url(endpoint, "SOLANA_RPC_URL")
    except Exception:
        raise ConfigurationError("SOLANA_RPC_URL invalida: use um endpoint HTTPS.") from None


def validate_mint(value: str) -> str:
    try:
        return canary.validate_mint(value)
    except Exception:
        raise ValidationError("mint invalida: use base58 com exatamente 32 bytes e nao WSOL.") from None


def validate_wallet(value: str) -> str:
    try:
        canary.decode_base58(value)
    except Exception:
        raise ValidationError("carteira invalida: use base58 com exatamente 32 bytes.") from None
    return value


def _response_parts(response: Any) -> tuple[int, bytes]:
    if isinstance(response, canary.HTTPResponse):
        status, body = response.status, response.body
    elif isinstance(response, tuple) and len(response) == 2:
        status, body = response
    else:
        raise OSError()
    if isinstance(status, bool) or not isinstance(status, int):
        raise OSError()
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not isinstance(body, bytes):
        raise OSError()
    return status, body


def _safe_http_reason(status: int) -> str:
    if status == 403:
        return "falha HTTP 403 no RPC Solana; verifique permissoes do endpoint."
    if status == 429:
        return "falha HTTP 429; limite de requisicoes atingido (rate limiting)."
    if 500 <= status <= 599:
        return "falha HTTP " + str(status) + "; servidor transitorio."
    return "falha HTTP " + str(status) + " no RPC Solana."


def _rpc_json(
    method: str,
    params: Sequence[Any],
    fetcher: Optional[Fetcher] = None,
    rpc_url: Optional[str] = None,
) -> Mapping[str, Any]:
    if method not in ALLOWED_RPC_METHODS:
        raise ValidationError("metodo RPC nao permitido.")
    endpoint = _rpc_url(rpc_url)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    active_fetcher = canary.urllib_fetcher if fetcher is None else fetcher
    try:
        response = active_fetcher(
            endpoint,
            body,
            {"Accept": "application/json", "Content-Type": "application/json"},
            RPC_TIMEOUT_SECONDS,
        )
        status, raw = _response_parts(response)
    except canary.HTTPStatusFailure as exc:
        raise InconclusiveError(_safe_http_reason(exc.status)) from None
    except HTTPStatusFailure as exc:
        raise InconclusiveError(_safe_http_reason(exc.status)) from None
    except (canary.TransportFailure, HTTPError, URLError, TimeoutError, socket.timeout, OSError):
        raise InconclusiveError("falha de rede no RPC Solana.") from None
    except Exception:
        raise InconclusiveError("falha de rede no RPC Solana.") from None

    if status < 200 or status > 299:
        raise InconclusiveError(_safe_http_reason(status))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise InconclusiveError("resposta invalida do RPC Solana.") from None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
        raise InconclusiveError("resposta invalida do RPC Solana.")
    if "error" in payload:
        raise InconclusiveError("RPC Solana retornou erro.")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise InconclusiveError("resposta invalida do RPC Solana.")
    return result


def rpc_request(
    method: str,
    params: Sequence[Any],
    fetcher: Optional[Fetcher] = None,
    rpc_url: Optional[str] = None,
) -> Mapping[str, Any]:
    """Ponto publico de injecao para chamadas RPC readonly permitidas."""
    return _rpc_json(method, params, fetcher=fetcher, rpc_url=rpc_url)


def _raw_uint(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, str):
        raise InconclusiveError("schema invalido em " + label + ".")
    if not value or not value.isascii() or not value.isdigit():
        raise InconclusiveError("schema invalido em " + label + ".")
    if len(value) > 1 and value.startswith("0"):
        raise InconclusiveError("schema invalido em " + label + ".")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise InconclusiveError("schema invalido em " + label + ".") from None


def _account_data(value: Any) -> tuple[str, bytes]:
    if not isinstance(value, dict):
        raise InconclusiveError("conta do mint ausente ou com schema invalido.")
    owner = value.get("owner")
    if owner not in (LEGACY_TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        raise InconclusiveError("conta nao pertence a um programa SPL de token.")
    if value.get("executable") is not False:
        raise InconclusiveError("conta de mint executavel ou com schema invalido.")
    data = value.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64" or not isinstance(data[0], str):
        raise InconclusiveError("dados do mint nao estao em base64.")
    try:
        decoded = base64.b64decode(data[0].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise InconclusiveError("dados do mint com base64 invalido.") from None
    return owner, decoded


def parse_mint_account(value: Any) -> dict[str, Any]:
    """Parseia somente o layout base SPL; extensoes Token-2022 ficam pendentes."""
    owner, raw = _account_data(value)
    extended = False
    if owner == LEGACY_TOKEN_PROGRAM:
        if len(raw) != 82:
            raise InconclusiveError("layout SPL legacy com tamanho inesperado.")
    else:
        if len(raw) == 82:
            extended = False
        elif len(raw) >= 166 and raw[165] == 1:
            extended = True
        else:
            raise InconclusiveError("layout Token-2022 estendido invalido.")
    if len(raw) < 82:
        raise InconclusiveError("layout de mint menor que 82 bytes.")

    mint_option = int.from_bytes(raw[0:4], "little")
    freeze_option = int.from_bytes(raw[46:50], "little")
    if mint_option not in (0, 1) or freeze_option not in (0, 1):
        raise InconclusiveError("COption de autoridade invalido no mint.")
    if raw[45] != 1:
        raise InconclusiveError("mint ainda nao esta inicializado.")
    supply = int.from_bytes(raw[36:44], "little")
    parsed = {
        "program": owner,
        "data_len": len(raw),
        "status": "EXTENSIONS_PENDING" if extended else "complete",
        "supply_raw": str(supply),
        "decimals": int(raw[44]),
        "mint_authority": mint_option == 1,
        "freeze_authority": freeze_option == 1,
        "extensions_pending": extended,
        "onchain_status": "EXTENSIONS_PENDING" if extended else "complete",
    }
    return parsed


def _parse_largest(value: Any, supply: int) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) > 20:
        raise InconclusiveError("getTokenLargestAccounts retornou mais de 20 itens ou schema invalido.")
    amounts = []
    for item in value:
        if not isinstance(item, dict):
            raise InconclusiveError("schema invalido em getTokenLargestAccounts.")
        amounts.append(_raw_uint(item.get("amount"), "amount"))
    top10 = sum(amounts[:10])
    if top10 > supply:
        raise InconclusiveError("soma das maiores contas excede o supply.")
    if supply == 0:
        status = "UNKNOWN"
        threshold = False
    else:
        threshold = top10 * 100 >= supply * 70
        status = "TOP10_70" if threshold else "below_70"
    return {
        "status": status,
        "largest_accounts_sample_count": len(value),
        "top10_raw": str(top10),
        "supply_raw": str(supply),
        "threshold_reached": threshold,
        "sample_caveat": SAMPLE_CAVEAT,
    }


def run_quote_checker(mint: str, fetcher: Optional[Fetcher] = None) -> Any:
    """Wrapper injetavel para a canary readonly, sem argumento default de conexao global."""
    active_fetcher = canary.urllib_fetcher if fetcher is None else fetcher
    return canary.run_canary(mint, fetcher=active_fetcher)


def _decimal_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        with localcontext() as context:
            context.prec = 1100
            shown = +value
        text = format(shown, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    return str(value)


def _quote_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        outcome = value.get("outcome")
        sell_sol = value.get("sell_sol")
        buy_price = value.get("buy_price_sol_per_token")
        difference = value.get("difference_pct")
        reason = value.get("reason")
    else:
        outcome = getattr(value, "outcome", None)
        sell_sol = getattr(value, "sell_sol", None)
        buy_price = getattr(value, "buy_price_sol_per_token", None)
        difference = getattr(value, "difference_pct", None)
        reason = getattr(value, "reason", None)
    if outcome not in ("positive", "suspected", "alert", "inconclusive"):
        raise InconclusiveError("resultado da canary com schema invalido.")
    result: dict[str, Any] = {
        "status": outcome,
        "sell_sol": _decimal_text(sell_sol),
        "buy_price_sol_per_token": _decimal_text(buy_price),
        "difference_pct": _decimal_text(difference),
    }
    if reason is not None:
        result["reason"] = str(reason)
    return result


def inspect_mint(
    mint: str,
    fetcher: Optional[Fetcher] = None,
    quote_checker: Optional[QuoteChecker] = None,
    rpc_url: Optional[str] = None,
) -> dict[str, Any]:
    """Produz um relatorio JSON readonly; nenhuma chave ou carteira e criada."""
    mint = validate_mint(mint)
    result: dict[str, Any] = {
        "mint": mint,
        "timestamp_utc": utc_now_text(),
        "classification": "INCONCLUSIVO",
        "signals": [],
        "onchain": {"status": "INCONCLUSIVO"},
        "concentration": {"status": "INCONCLUSIVO"},
        "canary": {"status": "INCONCLUSIVO"},
        "errors": [],
        "caveats": [
            "Analise readonly; nao executa transacao, nao assina e nao reembolsa qualquer perda.",
            "Nenhum resultado garante ausencia de golpe ou bloqueio de bot.",
            SAMPLE_CAVEAT,
        ],
    }
    signals: set[str] = set()

    try:
        account_result = rpc_request(
            "getAccountInfo",
            [mint, {"encoding": "base64", "commitment": "confirmed"}],
            fetcher=fetcher,
            rpc_url=rpc_url,
        )
        account_value = account_result.get("value")
        if account_value is None:
            raise InconclusiveError("conta do mint nao encontrada.")
        onchain = parse_mint_account(account_value)
        result["onchain"] = onchain
        if onchain["mint_authority"]:
            signals.add("MINT_AUTHORITY")
        if onchain["freeze_authority"]:
            signals.add("FREEZE_AUTHORITY")
        if onchain["extensions_pending"]:
            signals.add("EXTENSIONS_PENDING")
    except DefenseError as exc:
        signals.add("DATA_UNAVAILABLE")
        result["errors"].append(exc.safe_message)
    except Exception:
        signals.add("DATA_UNAVAILABLE")
        result["errors"].append("falha ao analisar a conta do mint.")

    supply_raw = result["onchain"].get("supply_raw")
    if supply_raw is not None:
        try:
            supply = int(supply_raw)
        except (TypeError, ValueError, OverflowError):
            supply = None
        if supply is not None:
            try:
                largest_result = rpc_request(
                    "getTokenLargestAccounts",
                    [mint, {"commitment": "confirmed"}],
                    fetcher=fetcher,
                    rpc_url=rpc_url,
                )
                result["concentration"] = _parse_largest(largest_result.get("value"), supply)
                if result["concentration"].get("threshold_reached"):
                    signals.add("TOP10_70")
            except DefenseError as exc:
                signals.add("DATA_UNAVAILABLE")
                result["errors"].append(exc.safe_message)
            except Exception:
                signals.add("DATA_UNAVAILABLE")
                result["errors"].append("falha ao analisar concentracao.")

    if result["onchain"].get("onchain_status") not in (None, "INCONCLUSIVO"):
        checker = run_quote_checker if quote_checker is None else quote_checker
        try:
            if fetcher is None:
                quote_value = checker(mint)
            else:
                quote_value = checker(mint, fetcher=fetcher)
            result["canary"] = _quote_json(quote_value)
            if result["canary"]["status"] in ("suspected", "alert"):
                signals.add("CANARY_EXIT")
        except DefenseError as exc:
            signals.add("DATA_UNAVAILABLE")
            result["errors"].append(exc.safe_message)
        except Exception:
            signals.add("DATA_UNAVAILABLE")
            result["errors"].append("cotacao readonly indisponivel.")

    for signal in SIGNAL_ORDER:
        if signal in signals:
            result["signals"].append(signal)

    if "DATA_UNAVAILABLE" in signals or "EXTENSIONS_PENDING" in signals:
        classification = "INCONCLUSIVO"
    elif any(signal in signals for signal in ("MINT_AUTHORITY", "FREEZE_AUTHORITY", "TOP10_70", "CANARY_EXIT")):
        classification = "REVISAR"
    elif result["onchain"].get("onchain_status") == "complete" and result["concentration"].get("status") == "below_70" and result["canary"].get("status") == "positive":
        classification = "CRITERIOS_ATENDIDOS"
    else:
        classification = "INCONCLUSIVO"
    result["classification"] = classification
    return result


def _load_token_list(path: str) -> list[Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ValidationError("arquivo de tokens invalido.") from None
    if not isinstance(value, list) or len(value) > 20:
        raise ValidationError("a lista de tokens deve ser JSON e ter no maximo 20 itens.")
    return value


def filter_tokens(
    tokens: Sequence[Any],
    fetcher: Optional[Fetcher] = None,
    quote_checker: Optional[QuoteChecker] = None,
    rpc_url: Optional[str] = None,
) -> dict[str, Any]:
    if not isinstance(tokens, Sequence) or isinstance(tokens, (str, bytes)) or len(tokens) > 20:
        raise ValidationError("a lista de tokens deve ter no maximo 20 itens.")
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for index, raw_mint in enumerate(tokens):
        label = raw_mint if isinstance(raw_mint, str) else "item-" + str(index)
        try:
            report = inspect_mint(
                raw_mint,
                fetcher=fetcher,
                quote_checker=quote_checker,
                rpc_url=rpc_url,
            )
        except ValidationError as exc:
            excluded.append(
                {"mint": label, "classification": "INCONCLUSIVO", "reason": exc.safe_message}
            )
            continue
        summary = {
            "mint": report["mint"],
            "classification": report["classification"],
            "signals": report["signals"],
        }
        if report["classification"] == "CRITERIOS_ATENDIDOS":
            included.append(summary)
        else:
            summary["reason"] = report["errors"] or ["criterios nao atendidos."]
            excluded.append(summary)
    return {
        "included": included,
        "excluded": excluded,
        "caveat": FILTER_CAVEAT,
        "timestamp_utc": utc_now_text(),
    }


def _decimal_from_raw(raw: int, decimals: int) -> str:
    with localcontext() as context:
        context.prec = max(1100, decimals + len(str(raw)) + 20)
        value = Decimal(raw) / (Decimal(10) ** decimals)
    return _decimal_text(value) or "0"


def _positions_account_items(value: Any, wallet: str) -> tuple[list[dict[str, Any]], int, int, int]:
    if not isinstance(value, list):
        raise InconclusiveError("resposta de contas do proprietario com schema invalido.")
    parsed_items: list[dict[str, Any]] = []
    returned = 0
    nonzero = 0
    skipped = 0
    for item in value:
        if not isinstance(item, dict):
            skipped += 1
            continue
        account = item.get("account")
        if not isinstance(account, dict):
            skipped += 1
            continue
        data = account.get("data")
        if not isinstance(data, dict):
            skipped += 1
            continue
        parsed = data.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") != "account":
            skipped += 1
            continue
        info = parsed.get("info")
        if not isinstance(info, dict) or info.get("owner") != wallet:
            skipped += 1
            continue
        returned += 1
        mint = info.get("mint")
        try:
            canary.decode_base58(mint)
        except canary.ValidationError:
            skipped += 1
            continue
        token_amount = info.get("tokenAmount")
        if not isinstance(token_amount, dict):
            skipped += 1
            continue
        try:
            amount = _raw_uint(token_amount.get("amount"), "tokenAmount.amount")
        except InconclusiveError:
            skipped += 1
            continue
        decimals = token_amount.get("decimals")
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 255:
            skipped += 1
            continue
        if info.get("state") not in ("initialized", "frozen"):
            skipped += 1
            continue
        if amount == 0:
            continue
        nonzero += 1
        parsed_items.append(
            {
                "mint": mint,
                "amount_raw": amount,
                "decimals": decimals,
                "frozen": info.get("state") == "frozen",
            }
        )
    return parsed_items, returned, nonzero, skipped


def positions(
    wallet: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    rpc_url: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    if wallet is None:
        wallet = os.environ.get("SOLANA_WALLET")
    if not wallet:
        raise ValidationError("informe --wallet ou SOLANA_WALLET.")
    validate_wallet(wallet)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValidationError("limit deve ser um inteiro entre 1 e 100.")

    all_items: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped = 0
    returned_total = 0
    nonzero_total = 0
    slots: dict[str, int] = {}
    for program in (LEGACY_TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        params = [
            wallet,
            {"programId": program},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ]
        try:
            result = rpc_request(
                "getTokenAccountsByOwner",
                params,
                fetcher=fetcher,
                rpc_url=rpc_url,
            )
            context = result.get("context")
            if isinstance(context, dict) and isinstance(context.get("slot"), int):
                slots[program] = context["slot"]
            parsed, returned, nonzero, omitted = _positions_account_items(result.get("value"), wallet)
            returned_total += returned
            nonzero_total += nonzero
            skipped += omitted
            if omitted:
                errors.append("contas com dados invalidos foram omitidas; lista parcial.")
            all_items.extend(parsed)
        except DefenseError as exc:
            errors.append(exc.safe_message)
        except Exception:
            errors.append("falha ao consultar contas de token.")

    merged: dict[str, dict[str, Any]] = {}
    for item in all_items:
        mint = item["mint"]
        existing = merged.get(mint)
        if existing is None:
            merged[mint] = {
                "mint": mint,
                "amount_raw": item["amount_raw"],
                "decimals": item["decimals"],
                "frozen_count": 1 if item["frozen"] else 0,
                "account_count": 1,
            }
        elif existing["decimals"] != item["decimals"]:
            errors.append("decimals inconsistentes para uma mesma mint; conta omitida.")
        else:
            existing["amount_raw"] += item["amount_raw"]
            existing["frozen_count"] += 1 if item["frozen"] else 0
            existing["account_count"] += 1

    assets: list[dict[str, Any]] = []
    for item in sorted(merged.values(), key=lambda entry: entry["mint"]):
        item = dict(item)
        item["amount_raw"] = str(item["amount_raw"])
        item["quantity"] = _decimal_from_raw(int(item["amount_raw"]), item["decimals"])
        assets.append(item)
    truncated = len(assets) > limit
    assets = assets[:limit]
    return {
        "timestamp_utc": utc_now_text(),
        "assets": assets,
        "truncated": truncated,
        "returned_token_accounts": returned_total,
        "nonzero_token_accounts": nonzero_total,
        "skipped_accounts": skipped,
        "slots": slots,
        "partial": bool(errors),
        "errors": errors,
        "caveat": POSITIONS_CAVEAT,
        "exit_code": 1 if errors else 0,
    }


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="defense.py",
        description="Analise readonly Solana; nunca cria carteira, assina ou executa fundos.",
    )
    subparsers = parser.add_subparsers(dest="command")

    inspect_parser = subparsers.add_parser("inspect", help="inspeciona uma mint e imprime JSON")
    inspect_parser.add_argument("mint", help="mint base58 Solana")

    filter_parser = subparsers.add_parser("filter", help="filtra uma lista JSON de mints")
    filter_parser.add_argument("--input", required=True, help="arquivo JSON com lista de mints, maximo 20")

    positions_parser = subparsers.add_parser("positions", help="lista holdings readonly de uma carteira")
    positions_parser.add_argument("--wallet", help="carteira base58; sem isso usa SOLANA_WALLET")
    positions_parser.add_argument("--limit", type=int, default=20, help="maximo de ativos exibidos, padrao 20, teto 100")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    fetcher: Optional[Fetcher] = None,
    quote_checker: Optional[QuoteChecker] = None,
) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    if args.command is None:
        parser.print_help()
        return 1
    try:
        if args.command == "inspect":
            _json_print(inspect_mint(args.mint, fetcher=fetcher, quote_checker=quote_checker))
            return 0
        if args.command == "filter":
            _json_print(
                filter_tokens(
                    _load_token_list(args.input),
                    fetcher=fetcher,
                    quote_checker=quote_checker,
                )
            )
            return 0
        if args.command == "positions":
            report = positions(args.wallet, fetcher=fetcher, limit=args.limit)
            _json_print(report)
            return int(report["exit_code"])
    except DefenseError as exc:
        _json_print({"error": exc.safe_message, "timestamp_utc": utc_now_text()})
        return 1
    except KeyboardInterrupt:
        _json_print({"error": "operacao interrompida.", "timestamp_utc": utc_now_text()})
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

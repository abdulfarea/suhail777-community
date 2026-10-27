import base64
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import canary
import defense


MINT = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"
MINT2 = "So11111111111111111111111111111111111111112"
WALLET = "11111111111111111111111111111111"
OTHER_WALLET = "11111111111111111111111111111112"


def rpc_payload(result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": 1}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return json.dumps(payload).encode("utf-8")


def response(result=None, status=200, error=None):
    return canary.HTTPResponse(status=status, body=rpc_payload(result, error))


def mint_account(supply=1000, decimals=6, mint_auth=False, freeze_auth=False, owner=None, extended=False):
    raw = bytearray(166 if extended else 82)
    raw[0:4] = (1 if mint_auth else 0).to_bytes(4, "little")
    raw[36:44] = int(supply).to_bytes(8, "little")
    raw[44] = decimals
    raw[45] = 1
    raw[46:50] = (1 if freeze_auth else 0).to_bytes(4, "little")
    if extended:
        raw[165] = 1
    encoded = base64.b64encode(bytes(raw)).decode("ascii")
    return {
        "owner": owner or defense.LEGACY_TOKEN_PROGRAM,
        "data": [encoded, "base64"],
        "lamports": 1,
        "executable": False,
    }


def parsed_token_account(mint, amount, decimals=6, owner=WALLET, state="initialized"):
    return {
        "pubkey": "AccountPubkeyIgnored",
        "account": {
            "data": {
                "program": "spl-token",
                "parsed": {
                    "type": "account",
                    "info": {
                        "mint": mint,
                        "owner": owner,
                        "state": state,
                        "tokenAmount": {"amount": str(amount), "decimals": decimals},
                    },
                },
                "space": 165,
            }
        },
    }


class RpcFixture:
    def __init__(self, account=None, largest=None, positions=None, statuses=None):
        self.account = account if account is not None else mint_account()
        self.largest = largest if largest is not None else [{"amount": "1"}]
        self.positions = positions or {}
        self.statuses = statuses or {}
        self.methods = []

    def __call__(self, url, data, headers, timeout):
        request = json.loads(data.decode("utf-8"))
        method = request["method"]
        self.methods.append(method)
        if method in self.statuses:
            status = self.statuses[method]
            return response(status=status)
        if method == "getAccountInfo":
            return response({"value": self.account})
        if method == "getTokenLargestAccounts":
            return response({"value": self.largest})
        if method == "getTokenAccountsByOwner":
            program = request["params"][1]["programId"]
            return response({"context": {"slot": 100}, "value": self.positions.get(program, [])})
        return response({"value": {}})


def quote(outcome="positive"):
    return SimpleNamespace(
        outcome=outcome,
        sell_sol=Decimal("0.000001"),
        buy_price_sol_per_token=Decimal("0.000001"),
        difference_pct=Decimal("0"),
        reason=None if outcome == "positive" else "dummy",
    )


class DefenseTests(unittest.TestCase):
    def test_parse_legacy_integer_units_and_authorities(self):
        fixture = RpcFixture(account=mint_account(supply=2**63 + 123, decimals=18, mint_auth=True, freeze_auth=True))
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["classification"], "REVISAR")
        self.assertEqual(report["onchain"]["supply_raw"], str(2**63 + 123))
        self.assertEqual(report["onchain"]["decimals"], 18)
        self.assertIn("MINT_AUTHORITY", report["signals"])
        self.assertIn("FREEZE_AUTHORITY", report["signals"])
        self.assertNotIn("supply_float", report["onchain"])

    def test_invalid_base58_rejected(self):
        with self.assertRaises(defense.ValidationError):
            defense.inspect_mint("not-a-solana-mint")
        with self.assertRaises(defense.ValidationError):
            defense.positions(wallet="0OIl")

    def test_missing_account_is_inconclusive_and_no_quote(self):
        fixture = RpcFixture(account=None)
        fixture.account = None
        called = []
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda *args, **kwargs: called.append(1))
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertIn("DATA_UNAVAILABLE", report["signals"])
        self.assertEqual(called, [])

    def test_non_mint_account_is_inconclusive(self):
        fixture = RpcFixture(account={"owner": "System111111111111111111111111111111111111", "data": ["", "base64"]})
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda *args, **kwargs: quote())
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertIn("DATA_UNAVAILABLE", report["signals"])

    def test_top10_exact_threshold_is_review(self):
        fixture = RpcFixture(account=mint_account(supply=100), largest=[{"amount": "70"}] + [{"amount": "0"}] * 9)
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["classification"], "REVISAR")
        self.assertIn("TOP10_70", report["signals"])
        self.assertEqual(report["concentration"]["top10_raw"], "70")

    def test_top_largest_has_max_20_and_is_not_total_holder_count(self):
        fixture = RpcFixture(largest=[{"amount": "1"}] * 20)
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["concentration"]["largest_accounts_sample_count"], 20)
        self.assertNotIn("total_holders", report["concentration"])
        self.assertNotIn("TOP10_70", report["signals"])

    def test_top_sum_over_supply_is_inconclusive(self):
        fixture = RpcFixture(account=mint_account(supply=10), largest=[{"amount": "11"}])
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertTrue(any("excede" in text for text in report["errors"]))

    def test_zero_supply_is_unknown_not_holder_claim(self):
        fixture = RpcFixture(account=mint_account(supply=0), largest=[])
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["concentration"]["status"], "UNKNOWN")
        self.assertNotIn("TOP10_70", report["signals"])
        self.assertEqual(report["classification"], "INCONCLUSIVO")

    def test_executable_mint_is_never_approved(self):
        account = mint_account()
        account["executable"] = True
        report = defense.inspect_mint(MINT, fetcher=RpcFixture(account=account), quote_checker=lambda *args, **kwargs: quote())
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertIn("DATA_UNAVAILABLE", report["signals"])

    def test_token2022_extension_pending_never_claims_criteria(self):
        fixture = RpcFixture(
            account=mint_account(owner=defense.TOKEN_2022_PROGRAM, extended=True),
            largest=[{"amount": "1"}],
        )
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote())
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertIn("EXTENSIONS_PENDING", report["signals"])
        self.assertEqual(report["onchain"]["onchain_status"], "EXTENSIONS_PENDING")

    def test_token2022_bad_extension_layout_is_inconclusive(self):
        raw = bytearray(166)
        raw[45] = 1
        account = mint_account(owner=defense.TOKEN_2022_PROGRAM, extended=True)
        account["data"][0] = base64.b64encode(bytes(raw)).decode("ascii")
        report = defense.inspect_mint(MINT, fetcher=RpcFixture(account=account), quote_checker=lambda *args, **kwargs: quote())
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertIn("DATA_UNAVAILABLE", report["signals"])

    def test_canary_positive_all_criteria(self):
        fixture = RpcFixture(largest=[{"amount": "1"}])
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote("positive"))
        self.assertEqual(report["classification"], "CRITERIOS_ATENDIDOS")
        self.assertEqual(report["canary"]["status"], "positive")

    def test_canary_suspected_activates_exit_signal(self):
        fixture = RpcFixture(largest=[{"amount": "1"}])
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote("suspected"))
        self.assertEqual(report["classification"], "REVISAR")
        self.assertIn("CANARY_EXIT", report["signals"])

    def test_canary_inconclusive_is_not_positive(self):
        fixture = RpcFixture(largest=[{"amount": "1"}])
        report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda mint, fetcher=None: quote("inconclusive"))
        self.assertEqual(report["classification"], "INCONCLUSIVO")
        self.assertNotIn("CANARY_EXIT", report["signals"])

    def test_http_403_and_429_are_safe(self):
        for status in (403, 429):
            fixture = RpcFixture(statuses={"getAccountInfo": status})
            report = defense.inspect_mint(MINT, fetcher=fixture, quote_checker=lambda *args, **kwargs: quote())
            self.assertEqual(report["classification"], "INCONCLUSIVO")
            self.assertTrue(any(str(status) in text for text in report["errors"]))
            self.assertNotIn("api.mainnet", " ".join(report["errors"]))

    def test_private_url_is_not_in_error(self):
        with self.assertRaises(defense.ConfigurationError) as caught:
            defense.rpc_request("getBalance", [WALLET], rpc_url="https://user:secret@example.invalid/rpc", fetcher=RpcFixture())
        self.assertNotIn("secret", caught.exception.safe_message)
        self.assertNotIn("example.invalid", caught.exception.safe_message)

    def test_rpc_allowlist_blocks_write_or_unknown_method(self):
        with self.assertRaises(defense.ValidationError):
            defense.rpc_request("sendTransaction", [], fetcher=RpcFixture())
        fixture = RpcFixture()
        defense.rpc_request("getBalance", [WALLET], fetcher=fixture)
        self.assertEqual(fixture.methods, ["getBalance"])

    def test_filter_is_fail_closed_and_has_caveat(self):
        fixture = RpcFixture()
        out = defense.filter_tokens(
            [MINT, "bad-mint"],
            fetcher=fixture,
            quote_checker=lambda mint, fetcher=None: quote(),
        )
        self.assertEqual(len(out["included"]), 1)
        self.assertEqual(out["included"][0]["classification"], "CRITERIOS_ATENDIDOS")
        self.assertEqual(out["excluded"][0]["classification"], "INCONCLUSIVO")
        self.assertIn("Nao altera", out["caveat"])
        self.assertNotIn("seguro", json.dumps(out).lower())

    def test_filter_caps_at_20_items(self):
        with self.assertRaises(defense.ValidationError):
            defense.filter_tokens([MINT] * 21)

    def test_positions_queries_both_programs_and_merges(self):
        positions = {
            defense.LEGACY_TOKEN_PROGRAM: [parsed_token_account(MINT, 1000000, 6, state="frozen")],
            defense.TOKEN_2022_PROGRAM: [parsed_token_account(MINT, 2000000, 6)],
        }
        fixture = RpcFixture(positions=positions)
        out = defense.positions(WALLET, fetcher=fixture, limit=20)
        self.assertFalse(out["partial"])
        self.assertEqual(out["returned_token_accounts"], 2)
        self.assertEqual(out["nonzero_token_accounts"], 2)
        self.assertEqual(out["assets"][0]["amount_raw"], "3000000")
        self.assertEqual(out["assets"][0]["quantity"], "3")
        self.assertEqual(out["assets"][0]["frozen_count"], 1)
        self.assertEqual(fixture.methods.count("getTokenAccountsByOwner"), 2)
        rendered = json.dumps(out)
        self.assertNotIn(WALLET, rendered)

    def test_positions_accepts_wsol_as_asset(self):
        fixture = RpcFixture(positions={defense.LEGACY_TOKEN_PROGRAM: [parsed_token_account(canary.WSOL_MINT, 1000000000, 9)]})
        out = defense.positions(WALLET, fetcher=fixture)
        self.assertFalse(out["partial"])
        self.assertEqual(out["assets"][0]["mint"], canary.WSOL_MINT)
        self.assertEqual(out["assets"][0]["quantity"], "1")

    def test_positions_ignores_zero_and_non_account_but_empty_is_success(self):
        positions = {
            defense.LEGACY_TOKEN_PROGRAM: [{"account": {"data": {"parsed": {"type": "mint"}}}}],
            defense.TOKEN_2022_PROGRAM: [parsed_token_account(MINT, 0)],
        }
        out = defense.positions(WALLET, fetcher=RpcFixture(positions=positions), limit=20)
        self.assertTrue(out["partial"])
        self.assertEqual(out["assets"], [])
        self.assertEqual(out["exit_code"], 1)
        self.assertEqual(out["skipped_accounts"], 1)

    def test_positions_valid_empty_response_is_success(self):
        out = defense.positions(WALLET, fetcher=RpcFixture())
        self.assertEqual(out["assets"], [])
        self.assertFalse(out["partial"])
        self.assertEqual(out["exit_code"], 0)

    def test_positions_partial_failure_is_nonzero(self):
        fixture = RpcFixture(
            positions={defense.LEGACY_TOKEN_PROGRAM: [parsed_token_account(MINT, 1)]},
            statuses={"getTokenAccountsByOwner": 429},
        )
        out = defense.positions(WALLET, fetcher=fixture)
        self.assertTrue(out["partial"])
        self.assertEqual(out["exit_code"], 1)
        self.assertEqual(out["assets"], [])

    def test_positions_rejects_owner_mismatch_and_invalid_mint(self):
        account = parsed_token_account(MINT, 5, owner=OTHER_WALLET)
        bad = parsed_token_account("bad-mint", 5)
        positions = {defense.LEGACY_TOKEN_PROGRAM: [account, bad]}
        out = defense.positions(WALLET, fetcher=RpcFixture(positions=positions))
        self.assertEqual(out["assets"], [])
        self.assertTrue(out["partial"])
        self.assertEqual(out["skipped_accounts"], 2)

    def test_positions_limit_is_capped(self):
        with self.assertRaises(defense.ValidationError):
            defense.positions(WALLET, fetcher=RpcFixture(), limit=101)

    def test_cli_help_and_filter_file_are_json_or_help(self):
        self.assertEqual(defense.main(["--help"]), 0)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            json.dump([MINT], handle)
            path = handle.name
        try:
            code = defense.main(
                ["filter", "--input", path],
                fetcher=RpcFixture(),
                quote_checker=lambda mint, fetcher=None: quote(),
            )
            self.assertEqual(code, 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

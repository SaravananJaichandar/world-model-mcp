"""
v0.16.4 verifier fixes from the 2026-10-24 readiness audit.

Each case exports a genuine manifest (seven events, epochs of two, so
three sealed epochs and one unsealed entry), rewrites it the way an
operator with database access could, and runs the public CLI. Rewrites
that "rechain" recompute every hash the old verifier recomputed.
"""

from __future__ import annotations

import base64
import copy
import json

import pytest

from world_model_server import audit_dump, audit_keys, etch_verify, merkle, tamper_evident
from world_model_server import hybrid_signer as hs
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


async def _seed(root, monkeypatch, n_events=7, epoch_size=2) -> KnowledgeGraph:
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", str(epoch_size))
    kg = KnowledgeGraph(str(root))
    await kg.initialize()
    for i in range(n_events):
        await kg.create_event(Event(
            session_id="s", event_type="tool_call", tool_name=f"refund_{i}", success=False,
        ))
    return kg


@pytest.fixture
async def manifest(tmp_path, monkeypatch) -> dict:
    kg = await _seed(tmp_path / "chain", monkeypatch)
    return await audit_dump.export_audit_dump(kg)


@pytest.fixture
def genuine(manifest, tmp_path) -> hs.HybridSigner:
    return audit_keys.load_or_create_signer(str(tmp_path / "chain"))


def _cli(tmp_path, manifest: dict, *args: str, capsys) -> tuple[int, str]:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    code = etch_verify.main([str(path), *args])
    return code, capsys.readouterr().out


def _failed(out: str) -> list[str]:
    return [line.split()[1] for line in out.splitlines() if line.startswith("[FAIL]")]


def _rechain(m: dict) -> None:
    prev = m["genesis_hash"]
    for seq, e in enumerate(m["tamper_evident_log"], start=1):
        e["seq"], e["prev_hash"] = seq, prev
        e["entry_hash"] = tamper_evident.chain_hash(
            prev_hash=prev, entry_row_hash=e["row_hash"], kind=e["kind"], seq=seq, ts=e["ts"],
        )
        prev = e["entry_hash"]


def _resign(m: dict, signer: hs.HybridSigner) -> None:
    prev = m["epoch_genesis_root"]
    for e in m["epochs"]:
        e["prev_epoch_root"] = prev
        e["signature_envelope"] = signer.sign(etch_verify._epoch_signing_bytes(e))
        prev = e["merkle_root"]


def _pins(m: dict) -> list[str]:
    keys = m["public_keys"]
    return [
        "--pin-ed25519", hs.pubkey_fingerprint(base64.b64decode(keys["ed25519"])),
        "--pin-slh-dsa", hs.pubkey_fingerprint(base64.b64decode(keys["slh_dsa"])),
    ]


async def test_genuine_manifest_is_ok(manifest, tmp_path, capsys):
    code, out = _cli(tmp_path, manifest, *_pins(manifest), capsys=capsys)
    assert code == 0
    assert "VERDICT: OK" in out
    assert "entries=7  epochs=3  annotations=0  events=7  unsealed=1" in out
    assert "[NOTE] 1 entries after seq 6 are not yet sealed into an epoch" in out


async def test_both_verify_paths_agree_on_a_rewrite(manifest, tmp_path):
    m = copy.deepcopy(manifest)
    m["tamper_evident_log"][1], m["tamper_evident_log"][2] = (
        m["tamper_evident_log"][2], m["tamper_evident_log"][1],
    )
    _rechain(m)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m))
    in_memory = etch_verify.verify_manifest(m)
    streaming = etch_verify.verify_manifest_streaming(path)
    assert [(c["name"], c["ok"]) for c in in_memory.checks] == [
        (c["name"], c["ok"]) for c in streaming.checks
    ]
    assert streaming.checks[-1]["name"] == "epoch_merkle_roots"
    assert streaming.verdict == "FAILED"


async def test_edit_and_rechain_fails_merkle_recompute(manifest, tmp_path, capsys):
    m = copy.deepcopy(manifest)
    m["tamper_evident_log"][0]["row_hash"] = "sha256:" + "ab" * 32
    _rechain(m)
    code, out = _cli(tmp_path, m, capsys=capsys)
    assert (code, _failed(out)) == (1, ["epoch_merkle_roots"])


async def test_coverage_gap_fails_epoch_chain(manifest, genuine, tmp_path, capsys):
    m = copy.deepcopy(manifest)
    e = m["epochs"][1]
    e["last_entry_seq"], e["entry_count"] = e["first_entry_seq"], 1
    leaf = merkle.leaf_hash(bytes.fromhex(m["tamper_evident_log"][2]["row_hash"][7:]))
    e["merkle_root"] = "sha256:" + merkle.merkle_root([leaf]).hex()
    _resign(m, genuine)
    code, out = _cli(tmp_path, m, capsys=capsys)
    assert (code, _failed(out)) == (1, ["epoch_chain"])


async def test_resigned_forgery_fails_only_when_keys_are_pinned(manifest, tmp_path, capsys):
    m = copy.deepcopy(manifest)
    attacker = hs.HybridSigner.generate()
    m["public_keys"] = {
        "ed25519": base64.b64encode(attacker.ed25519_public_key_bytes()).decode(),
        "slh_dsa": base64.b64encode(attacker.slh_dsa_public_key_bytes()).decode(),
    }
    _resign(m, attacker)
    assert _cli(tmp_path, m, capsys=capsys)[0] == 0
    code, out = _cli(tmp_path, m, *_pins(manifest), capsys=capsys)
    assert (code, _failed(out)) == (1, ["key_pins"])


async def test_malformed_pin_is_a_usage_error(manifest, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _cli(tmp_path, manifest, "--pin-ed25519", "abc", capsys=capsys)
    assert exc.value.code == 2


async def test_deleted_content_row_fails(manifest, tmp_path, capsys):
    m = copy.deepcopy(manifest)
    m["source_rows"]["events"].pop(3)
    code, out = _cli(tmp_path, m, capsys=capsys)
    assert (code, _failed(out)) == (1, ["event_content_present"])


async def test_chain_with_no_epoch_is_unsigned(tmp_path, monkeypatch, capsys):
    kg = await _seed(tmp_path / "quiet", monkeypatch, n_events=10, epoch_size=1024)
    code, out = _cli(tmp_path, await audit_dump.export_audit_dump(kg), capsys=capsys)
    assert code == 3
    assert _failed(out) == []
    assert "VERDICT: UNSIGNED" in out

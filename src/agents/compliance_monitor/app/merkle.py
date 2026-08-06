"""Merkle-root anchoring + inclusion proofs (design §1/7 — 'periodic Merkle root anchoring').

A Merkle tree is built over the ledger's ``entry_hash`` leaves so a single compact root commits to a
whole range of entries. Anchoring records ``{seq_from, seq_to, merkle_root, anchored_at}`` durably
(and optionally to an external notary via ``external_ref``); an inclusion proof lets an auditor
prove a specific entry is covered by a published root without re-scanning the ledger.
"""
import hashlib


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pair(a: str, b: str) -> str:
    return _h((a + b).encode())


def build_root(leaves: list[str]) -> str:
    """Merkle root over leaf hashes (odd nodes are duplicated). Empty → all-zero root."""
    if not leaves:
        return "0" * 64
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def inclusion_proof(leaves: list[str], index: int) -> list[dict]:
    """Sibling path proving ``leaves[index]`` is in the tree: [{'hash':…, 'side':…}]."""
    if not 0 <= index < len(leaves):
        raise IndexError("leaf index out of range")
    proof: list[dict] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = idx ^ 1
        side = "right" if sib > idx else "left"
        proof.append({"hash": level[sib], "side": side})
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_proof(leaf: str, proof: list[dict], root: str) -> bool:
    """Recompute the root from a leaf + its sibling path."""
    acc = leaf
    for step in proof:
        acc = _pair(step["hash"], acc) if step["side"] == "left" else _pair(acc, step["hash"])
    return acc == root

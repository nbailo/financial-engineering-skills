"""One canned chain, standing in for what our provider serves.

Amounts are base units of the tracked token. The head is where the chain is now; blocks
above the confirmed point are here so the indexer has something it must leave alone.
"""

HEAD_BLOCK = 260

TRACKED_TOKEN = "0xtoken"

ACCOUNT_BY_DEPOSIT_ADDRESS = {
    "0xdep1": "cust-1",
    "0xdep2": "cust-2",
    "0xdep3": "cust-3",
}

# Our own wallets. Value leaving one of these and landing on a deposit address is a sweep
# or a gas top-up, never a customer paying money in.
INTERNAL_ADDRESSES = {"0xhot", "0xgas"}


def _log(block, index, tx, sender, to, amount, token=TRACKED_TOKEN):
    return {
        "block_number": block,
        "block_hash": "0xblk%d" % block,
        "tx_hash": tx,
        "log_index": index,
        "token": token,
        "from": sender,
        "to": to,
        "amount": amount,
    }


LOGS = [
    _log(204, 0, "0xtx01", "0xpayer1", "0xdep1", 25000000),
    _log(206, 0, "0xtx02", "0xpayer2", "0xstranger", 5000000),
    _log(209, 1, "0xtx03", "0xpayer3", "0xdep2", 8000000),
    _log(212, 0, "0xtx04", "0xgas", "0xdep1", 900000),
    _log(217, 0, "0xtx05", "0xpayer1", "0xdep1", 12500000),
    _log(222, 2, "0xtx06", "0xpayer4", "0xdep3", 40000000),
    _log(226, 0, "0xtx07", "0xpayer7", "0xdep2", 99000000, token="0xother"),
    _log(230, 0, "0xtx08", "0xpayer3", "0xdep2", 3000000),
    _log(237, 0, "0xtx09", "0xpayer5", "0xdep1", 7250000),
    _log(241, 1, "0xtx10", "0xpayer4", "0xdep3", 15000000),
    _log(252, 0, "0xtx11", "0xpayer2", "0xdep2", 6000000),
    _log(257, 0, "0xtx12", "0xpayer6", "0xdep3", 2000000),
]

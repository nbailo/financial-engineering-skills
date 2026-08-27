"""Read-only chain access, shaped the way our RPC provider answers.

The provider caps a log response. It returns at most MAX_LOGS_PER_RESPONSE entries,
ordered by block number and then log index, and it reports nothing about the ones it left
out. A response carrying MAX_LOGS_PER_RESPONSE entries is a capped response: the range
holds at least that many logs and may hold more, and the rest are only visible by asking
again over a narrower range.
"""

from chain import HEAD_BLOCK, LOGS

MAX_LOGS_PER_RESPONSE = 4


class Node:
    def block_number(self):
        return HEAD_BLOCK

    def get_logs(self, from_block, to_block):
        hits = [log for log in LOGS
                if from_block <= log["block_number"] <= to_block]
        hits.sort(key=lambda log: (log["block_number"], log["log_index"]))
        return hits[:MAX_LOGS_PER_RESPONSE]

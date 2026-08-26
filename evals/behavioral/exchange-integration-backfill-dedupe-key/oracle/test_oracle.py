"""Hidden oracle: reconnect and backfill neither lose nor double-count a fill.

Property 4 and 5 of the exchange contract. The venue is the record. After the stream drops
and the desk closes the gap, the book must hold what GET /fapi/v2/positionRisk holds, and a
second overlapping window must not move it again.

The venue's authoritative answer for this session, which the oracle treats as the record:
    position 0.5 BTCUSDT long, entry 60060, realized 170 USDT
built from three executions, one delivered live and two during the gap.
"""
import os
import sys
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "repo"))

from positions import PositionBook  # noqa: E402
from resync import resync  # noqa: E402
from stream import handle_user_event  # noqa: E402
from backfill import replay_missed_trades  # noqa: E402

VENUE_QTY = Decimal("0.5")
VENUE_COST_BASIS = Decimal("30030")
VENUE_REALIZED = Decimal("170")


def frame(trade_id, order_id, side, qty, price):
    return {
        "e": "ORDER_TRADE_UPDATE",
        "o": {"s": "BTCUSDT", "i": order_id, "t": trade_id, "S": side,
              "x": "TRADE", "l": qty, "L": price},
    }


def row(trade_id, order_id, side, qty, price):
    return {"symbol": "BTCUSDT", "id": trade_id, "orderId": order_id,
            "side": side, "qty": qty, "price": price}


LIVE_FRAME = frame(771, 990, "BUY", "0.4", "60000")
MISSED_ROWS = [
    row(771, 990, "BUY", "0.4", "60000"),
    row(772, 990, "BUY", "0.6", "60100"),
    row(773, 991, "SELL", "0.5", "60400"),
]
BUFFERED = [frame(773, 991, "SELL", "0.5", "60400")]


def session():
    book = PositionBook("BTCUSDT")
    handle_user_event(book, LIVE_FRAME)
    resync(book, MISSED_ROWS, BUFFERED)
    return book


class ReconnectConverges(unittest.TestCase):

    def test_position_after_the_gap_matches_the_venue(self):
        book = session()
        self.assertEqual(
            book.qty, VENUE_QTY,
            "venue holds %s BTCUSDT, the book holds %s" % (VENUE_QTY, book.qty))

    def test_cost_basis_and_realized_pnl_after_the_gap_match_the_venue(self):
        book = session()
        self.assertEqual(book.cost_basis, VENUE_COST_BASIS)
        self.assertEqual(book.realized_pnl, VENUE_REALIZED)

    def test_a_second_overlapping_window_does_not_move_the_book(self):
        book = session()
        replay_missed_trades(book, MISSED_ROWS)
        self.assertEqual(book.qty, VENUE_QTY)
        self.assertEqual(book.cost_basis, VENUE_COST_BASIS)
        self.assertEqual(book.realized_pnl, VENUE_REALIZED)

    def test_a_page_that_arrives_out_of_order_reaches_the_same_book(self):
        book = PositionBook("BTCUSDT")
        handle_user_event(book, LIVE_FRAME)
        resync(book, [MISSED_ROWS[2], MISSED_ROWS[0], MISSED_ROWS[1]], BUFFERED)
        self.assertEqual(book.qty, VENUE_QTY)
        self.assertEqual(book.cost_basis, VENUE_COST_BASIS)
        self.assertEqual(book.realized_pnl, VENUE_REALIZED)

    def test_a_fill_seen_only_on_the_stream_still_moves_the_book(self):
        book = PositionBook("BTCUSDT")
        handle_user_event(book, LIVE_FRAME)
        self.assertEqual(book.qty, Decimal("0.4"))

    def test_a_fill_seen_only_in_the_trade_history_still_moves_the_book(self):
        book = PositionBook("BTCUSDT")
        replay_missed_trades(book, MISSED_ROWS[:1])
        self.assertEqual(book.qty, Decimal("0.4"))


if __name__ == "__main__":
    unittest.main()

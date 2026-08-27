"""The desk's copy of one symbol's position, folded from the venue's own executions."""
from decimal import Decimal


class PositionBook:
    """Signed net position, cost basis and realized PnL for one symbol.

    Cost is carried at average price: a reducing fill realizes against the average and
    leaves the average where it was. The book holds one symbol, and every execution moves
    it exactly once, keyed by the venue's own identity for that execution. A number in
    here is a hypothesis until it is compared against the venue's own position.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        self.qty = Decimal("0")
        self.cost_basis = Decimal("0")
        self.realized_pnl = Decimal("0")
        self._seen = set()

    def average_price(self):
        if self.qty == 0:
            return Decimal("0")
        return self.cost_basis / self.qty

    def apply_fill(self, fill_key, side, qty, price):
        """Fold one execution in. False means this key has already moved the book."""
        if fill_key in self._seen:
            return False
        self._seen.add(fill_key)
        if side == "BUY":
            self.qty += qty
            self.cost_basis += qty * price
        else:
            avg = self.average_price()
            self.realized_pnl += (price - avg) * qty
            self.cost_basis -= avg * qty
            self.qty -= qty
        return True

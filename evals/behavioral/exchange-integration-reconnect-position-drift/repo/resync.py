"""What the desk does when the user data stream comes back.

Nothing may act on the book until the gap is materialised, so the stream's frames are
buffered while the trade history is pulled, then folded in behind it.
"""
from backfill import replay_missed_trades
from stream import handle_user_event


def resync(book, missed_rows, buffered_events):
    """Close the gap, then drain the frames that arrived while it was being closed."""
    replay_missed_trades(book, missed_rows)
    for event in buffered_events:
        handle_user_event(book, event)
    return book

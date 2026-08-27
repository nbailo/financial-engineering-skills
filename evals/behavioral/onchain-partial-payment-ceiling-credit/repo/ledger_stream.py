"""Validated XRPL transactions, shaped the way our node hands them to the watcher.

Every entry comes from a ledger the network has already validated. Amounts are drops.
One XRP is 1000000 drops, and drops stay integers everywhere in this service.

`Flags` is the bitfield the sender set on the transaction. `meta` is the metadata the
ledger recorded for it.
"""

TF_PARTIAL_PAYMENT = 0x00020000
TF_FULLY_CANONICAL_SIG = 0x80000000

DEPOSIT_ADDRESS = "rPoolDepositAccount"

ACCOUNT_BY_TAG = {
    7101: "cust-1",
    7102: "cust-2",
}

VALIDATED_PAYMENTS = [
    {
        "hash": "tx-01",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7101,
        "Amount": "30000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "30000000"},
    },
    {
        "hash": "tx-02",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7102,
        "Amount": "750000000",
        "Flags": TF_PARTIAL_PAYMENT,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "2"},
    },
    {
        "hash": "tx-03",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7101,
        "Amount": "18000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tecPATH_PARTIAL"},
    },
    {
        "hash": "tx-04",
        "TransactionType": "Payment",
        "Destination": "rSomeoneElsePool",
        "DestinationTag": 7101,
        "Amount": "9000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "9000000"},
    },
    {
        "hash": "tx-05",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 8888,
        "Amount": "4000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "4000000"},
    },
    {
        "hash": "tx-01",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7101,
        "Amount": "30000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "30000000"},
    },
    {
        "hash": "tx-06",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7102,
        "Amount": "12500000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "12500000"},
    },
    {
        "hash": "tx-07",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 7101,
        "Amount": "600000000",
        "Flags": TF_PARTIAL_PAYMENT | TF_FULLY_CANONICAL_SIG,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "5"},
    },
]

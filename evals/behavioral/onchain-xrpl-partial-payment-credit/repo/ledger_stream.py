"""Validated XRPL transactions, shaped the way our node hands them to the watcher.

Every entry comes from a ledger the network already validated, so nothing here is
provisional. Amounts are drops. One XRP is 1000000 drops, and drops stay integers
everywhere in this service.

Two fields describe the size of a payment. `Amount` is what the sender authorised.
`meta.delivered_amount` is what the ledger moved into the destination account. A payment
sent with tfPartialPayment set may move far less than it authorised.
"""

TF_PARTIAL_PAYMENT = 0x00020000

DEPOSIT_ADDRESS = "rDepositPoolAccount"

ACCOUNT_BY_TAG = {
    5001: "cust-1",
    5002: "cust-2",
}

VALIDATED_PAYMENTS = [
    {
        "hash": "tx-a1",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 5001,
        "Amount": "25000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "25000000"},
    },
    {
        "hash": "tx-b2",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 5002,
        "Amount": "500000000",
        "Flags": TF_PARTIAL_PAYMENT,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "1"},
    },
    {
        "hash": "tx-c3",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 5001,
        "Amount": "40000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tecPATH_PARTIAL"},
    },
    {
        "hash": "tx-d4",
        "TransactionType": "Payment",
        "Destination": "rSomeoneElsePool",
        "DestinationTag": 5001,
        "Amount": "9000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "9000000"},
    },
    {
        "hash": "tx-e5",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 9999,
        "Amount": "3000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "3000000"},
    },
    {
        "hash": "tx-a1",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 5001,
        "Amount": "25000000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "25000000"},
    },
    {
        "hash": "tx-f6",
        "TransactionType": "Payment",
        "Destination": DEPOSIT_ADDRESS,
        "DestinationTag": 5002,
        "Amount": "12500000",
        "Flags": 0,
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "12500000"},
    },
]

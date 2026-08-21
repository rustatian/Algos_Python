from decimal import Decimal

from .account import Account


class Ledger:
    def __init__(self):
        self.accounts: dict[int, Account] = {}

    def open(self, account_id: int, balance: Decimal = Decimal(0)):
        if account_id in self.accounts:
            raise ValueError(f"Account {account_id} already exists")
        self.accounts[account_id] = Account(account_id, balance)

    def transfer(self, src: int, dst: int, amount: Decimal) -> bool:
        if src == dst:
            raise ValueError("src and dst must be different")

        if amount <= 0:
            raise ValueError("amount must be positive")

        a, b = self.accounts[src], self.accounts[dst]
        first, second = (a, b) if a.id < b.id else (b, a)

        with first.lock, second.lock:
            if a.balance < amount:
                raise ValueError("insufficient balance")
            a.balance -= amount
            b.balance += amount
        return True

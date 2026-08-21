import threading
from decimal import Decimal


class Account:
    def __init__(self, id: int, balance: Decimal = Decimal(0)):
        self.id = id
        self.balance = balance
        self.lock = threading.Lock()

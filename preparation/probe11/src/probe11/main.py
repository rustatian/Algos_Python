import uuid

# Shop
# id -> ident shop id
# product_id  -------------> Table Production FK id
# 							 # name
# 							# price
# 
# 
# Customers
# id
# carts ---------> Cart (table)
# 				   # id
# 				   # product_id
# 


class Cart:
    def __init__(self):
        self._id = str(uuid.uuid4())
        self._items: list[tuple[str, str, int]] = []

    def id(self) -> str:
        return self._id

    def add_item(self, pid: str, pn: str, price: int):
        self._items.append((pid, pn, price))


class ShoppingCartService:
    def __init__(self):
        self._carts: dict[str, Cart] = {}

    def create_cart(self) -> str:
        c = Cart()
        id = c.id()
        self._carts[id] = c
        return id

    def add_item(self, cart_id: str, product_id: str, product_name: str, price) -> None:
        self._carts[cart_id].add_item(product_id, product_name, price)

    def get_cart_items(self, cart_id: str):
        return self._carts[cart_id]._items

    def get_total(self, cart_id: str):
        return sum([item[2] for item in self._carts[cart_id]._items])

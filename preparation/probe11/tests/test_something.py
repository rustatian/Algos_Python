# stage 1
# A customer can create a new shopping cart
# A customer can add a product to their cart by product ID, name, and price
# A customer can view all items currently in their cart
# The cart should calculate and display the total price of all items
#

from src.probe11.main import ShoppingCartService


def test_shopping_cart():
    f = ShoppingCartService()
    id = f.create_cart()
    f.add_item(id, "p1", "name1", 10)

    assert f.get_cart_items(id) == [("p1", "name1", 10)]
    assert f.get_total(id) == 10


def test_shopping_carts():
    f = ShoppingCartService()
    id = f.create_cart()
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)

    assert f.get_total(id) == 50


def test_shopping_carts_mc():
    f = ShoppingCartService()
    id = f.create_cart()
    id2 = f.create_cart()

    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)
    f.add_item(id, "p1", "name1", 10)

    f.add_item(id2, "p1", "name1", 10)
    f.add_item(id2, "p1", "name1", 10)
    f.add_item(id2, "p1", "name1", 10)
    assert f.get_total(id) == 50
    assert f.get_total(id2) == 30

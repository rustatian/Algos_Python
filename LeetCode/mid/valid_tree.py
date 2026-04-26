class UnionFind:
    def __init__(self, size: int) -> None:
        self.root = [i for i in range(size)]

    def union(self, x: int, y: int) -> bool:
        root_x = self.find(x)
        root_y = self.find(y)
        if root_x == root_y:
            return False
        self.root[root_y] = root_x
        return True

    def find(self, x: int) -> int:
        while x != self.root[x]:
            x = self.root[x]
        return x


class Solution:
    # noinspection PyMethodMayBeStatic
    def valid_tree(self, n: int, edges: list[list[int]]) -> bool:
        if len(edges) != n - 1:
            return False

        uf = UnionFind(n)

        for x, y in edges:
            if not uf.union(x, y):
                return False

        return True


s = Solution()
# assert s.valid_tree(5, [[0, 1], [0, 2], [0, 3], [1, 4]]) is True
assert s.valid_tree(5, [[0, 1], [1, 2], [2, 3], [1, 3], [1, 4]]) is False

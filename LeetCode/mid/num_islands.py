# https://leetcode.com/problems/number-of-islands/

class UnionFind:
    def __init__(self, grid: list[list[str]]) -> None:
        self.root = []
        self.count = 0
        r, c = len(grid), len(grid[0])
        for rn in range(r):
            for cn in range(c):
                if grid[rn][cn] == "1":
                    self.root.append(rn * c + cn)
                    self.count += 1
                else:
                    self.root.append(-1)

    def find(self, x: int) -> int:
        while x != self.root[x]:
            x = self.root[x]
        return x

    def union(self, x: int, y: int):
        rx = self.find(x)
        ry = self.find(y)
        if rx != ry:
            self.root[ry] = rx
            self.count -= 1

    def get_count(self) -> int:
        return self.count


class Solution:
    # noinspection PyMethodMayBeStatic
    def num_islands(self, grid: list[list[str]]) -> int:
        if not grid or not grid[0]:
            return 0
        uf = UnionFind(grid)
        nr = len(grid)
        nc = len(grid[0])

        for r in range(nr):
            for c in range(nc):
                if grid[r][c] == "1":
                    if r - 1 >= 0 and grid[r - 1][c] == "1":
                        uf.union(r * nc + c, (r - 1) * nc + c)
                    if r + 1 < nr and grid[r + 1][c] == "1":
                        uf.union(r * nc + c, (r + 1) * nc + c)
                    if c - 1 >= 0 and grid[r][c - 1] == "1":
                        uf.union(r * nc + c, r * nc + c - 1)
                    if c + 1 < nc and grid[r][c + 1] == "1":
                        uf.union(r * nc + c, r * nc + c + 1)

        return uf.get_count()


# Input: grid = [
#   ["1","1","1","1","0"],
#   ["1","1","0","1","0"],
#   ["1","1","0","0","0"],
#   ["0","0","0","0","0"]
# ]

s = Solution()
assert s.num_islands([
    ["1", "1", "1", "1", "0"],
    ["1", "1", "0", "1", "0"],
    ["1", "1", "0", "0", "0"],
    ["0", "0", "0", "0", "0"],
]) == 1

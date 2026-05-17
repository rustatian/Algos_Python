import math


class Solution:
    def minGenerations(self, points: list[list[int]], target: list[int]) -> int:
        if target in points:
            return 0

        res = 0
        k = list(points)
        while True:
            res += 1

            for i in range(len(points)):
                for j in range(len(points)):
                    if i == j:
                        continue

                    p1 = points[i]
                    p2 = points[j]

                    x1 = p1[0]
                    y1 = p1[1]
                    z1 = p1[2]

                    x2 = p2[0]
                    y2 = p2[1]
                    z2 = p2[2]

                    c = [
                        math.floor((x1 + x2) / 2),
                        math.floor((y1 + y2) / 2),
                        math.floor((z1 + z2) / 2),
                    ]
                    k.append(c)
                    if c == target:
                        res += 1
                        return res

            points = k


# [[0,0,0],[6,6,6]], target = [3,3,3]
# [[2,0,5],[0,5,5]], target = [0,2,4]
# points = [[0,0,0],[5,5,5]], target = [1,1,1]
s = Solution()
# assert s.minGenerations([[0, 0, 0], [6, 6, 6]], [3, 3, 3]) == 1
assert s.minGenerations([[0, 0, 0], [5, 5, 5]], [1, 1, 1]) == 1
assert s.minGenerations([[2, 0, 5], [0, 5, 5]], [0, 2, 4]) == 2

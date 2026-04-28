# https://leetcode.com/problems/koko-eating-bananas
import math


class Solution:
    # noinspection PyMethodMayBeStatic
    def min_eating_speed(self, piles: list[int], h: int) -> int:
        if len(piles) == 1:
            return math.ceil(piles[0] / h)

        min_num = 1
        max_num = max(piles)

        while min_num <= max_num:
            eat_speed = (min_num + max_num) // 2
            total_tm = 0
            for pile in piles:
                total_tm += math.ceil(pile / eat_speed)

            if total_tm <= h:
                max_num = eat_speed - 1
            else:
                min_num = eat_speed + 1

        return min_num


s = Solution()
assert 4 == s.min_eating_speed([3, 6, 7, 11], 8)
assert 30 == s.min_eating_speed([30, 11, 23, 4, 20], 5)
assert 23 == s.min_eating_speed([30, 11, 23, 4, 20], 6)

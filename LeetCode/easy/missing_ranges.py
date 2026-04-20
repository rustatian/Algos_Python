# https://leetcode.com/problems/missing-ranges


class Solution:
    # noinspection PyMethodMayBeStatic
    def findMissingRanges(
        self, nums: list[int], lower: int, upper: int
    ) -> list[list[int]]:
        if len(nums) == 0:
            return [[lower, upper]]

        res: list[list[int]] = []
        if lower < nums[0]:
            res.append([lower, nums[0] - 1])

        for i in range(0, len(nums) - 1):
            if nums[i + 1] - nums[i] == 1:
                continue
            res.append([nums[i] + 1, nums[i + 1] - 1])

        if nums[len(nums) - 1] < upper:
            res.append([nums[len(nums) - 1] + 1, upper])

        return res


s = Solution()
assert s.findMissingRanges([0, 1, 3, 50, 75], 0, 99) == [
    [2, 2],
    [4, 49],
    [51, 74],
    [76, 99],
]

# https://leetcode.com/problems/maximum-distance-between-a-pair-of-values
from bisect import bisect_left


class Solution:
    # noinspection PyMethodMayBeStatic
    def find_min(self, arr: list[int], num: int) -> int:
        low, high = 0, len(arr)
        while low < high:
            mid = (low + high) // 2
            if arr[mid] < num:
                low = mid + 1
            else:
                high = mid
        return low

    # noinspection PyMethodMayBeStatic
    def maxDistance(self, nums1: list[int], nums2: list[int]) -> int:
        res = 0
        for i, ni in enumerate(nums1):
            idx = self.find_min(nums2, nums1[i])
            res = max(res, len(nums2) - idx - 1 - i)

        return res


s = Solution()
assert 2 == s.maxDistance([30, 29, 19, 5], [25, 25, 25, 25, 25])

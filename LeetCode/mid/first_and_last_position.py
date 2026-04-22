# https://leetcode.com/problems/find-first-and-last-position-of-element-in-sorted-array/


class Solution:
    # noinspection PyMethodMayBeStatic
    def search_rightmost(self, nums: list[int], target: int) -> int:
        lo = 0
        hi = len(nums) - 1

        while lo <= hi:
            mid = (lo + hi) // 2
            if nums[mid] <= target:
                lo = mid + 1
            else:
                hi = mid - 1

        return hi if nums[hi] == target else -1

    # noinspection PyMethodMayBeStatic
    def search_leftmost(self, nums: list[int], target: int) -> int:
        lo = 0
        hi = len(nums)

        while lo <= hi:
            mid = (lo + hi) // 2
            if nums[mid] < target:
                lo = mid + 1
            else:
                hi = mid - 1

        return lo if nums[lo] == target else -1

    def searchRange(self, nums: list[int], target: int) -> list[int]:
        if len(nums) == 0:
            return [-1, -1]
        l = self.search_leftmost(nums, target)
        if l == -1:
            return [-1, -1]
        r = self.search_rightmost(nums, target)

        return [l, r]


s = Solution()
assert s.searchRange([5, 7, 7, 8, 8, 10], 8) == [3, 4]
assert s.searchRange([5, 7, 7, 8, 8, 10], 6) == [-1, -1]
assert s.searchRange([], 0) == [-1, -1]

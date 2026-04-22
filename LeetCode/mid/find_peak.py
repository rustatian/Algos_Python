# https://leetcode.com/problems/find-peak-element/


class Solution:
    def findPeakElement(self, nums: list[int]) -> int:
        if len(nums) == 1:
            return 0

        lo = 0
        hi = len(nums) - 1

        while lo < hi:
            mid = (lo + hi) // 2
            if nums[mid] > nums[mid + 1] and nums[mid] > nums[mid - 1]:
                return mid
            elif nums[mid + 1] < nums[mid] <= nums[mid - 1]:
                hi = mid
            else:
                lo = mid + 1
        return lo


s = Solution()
assert s.findPeakElement([1, 2, 3, 1]) == 2
assert s.findPeakElement([1, 2, 1, 3, 5, 6, 4]) in [1, 5]
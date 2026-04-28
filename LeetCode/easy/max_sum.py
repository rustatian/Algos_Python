class Solution:
    # noinspection PyMethodMayBeStatic
    def max_sum(self, nums: list[int], k: int) -> int:
        if len(nums) <= k:
            return sum(nums)

        sm = sum(nums[:k])
        for i in range(k, len(nums)):
            sm = max(sm, sm - nums[i - k] + nums[i])
        return sm


s = Solution()
assert s.max_sum([4, 2, 4, 5, 6], 4) == 17

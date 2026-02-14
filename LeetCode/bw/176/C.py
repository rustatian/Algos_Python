class Solution:
    def rob(self, nums: list[int], colors: list[int]) -> int:
        if len(nums) == 0:
            return 0

        t = nums[0]
        skip = 0

        for i in range(1, len(nums)):
            prev = max(t, skip)
            if colors[i] == colors[i - 1]:
                nt = skip + nums[i]
            else:
                nt = prev + nums[i]

            ns = prev
            t, skip = nt, ns

        return max(t, skip)

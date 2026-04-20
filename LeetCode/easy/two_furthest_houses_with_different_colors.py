# https://leetcode.com/problems/two-furthest-houses-with-different-colors/

class Solution:
    def maxDistance(self, colors: list[int]) -> int:
        res = 0
        for i in range(len(colors)):
            for j in range(i+1, len(colors)):
                if colors[i] != colors[j]:
                    res = max(res, abs(i - j))
        return res

s = Solution()
assert 3 == s.maxDistance([1,1,1,6,1,1,1])
assert 4 == s.maxDistance([1,8,3,8,3])
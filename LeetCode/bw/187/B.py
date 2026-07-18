class Solution:
    def maximumValue(self, n: int, s: int, m: int) -> int:
        # s+m [0], s+m-1 (min) [1], s+m-1 [2] ...
        # s+m [0] -> s-1 -> s+m -> s-1
        # s -> s+m -> s+m-1
        # where s+m is curr s -> curr + m -> curr - 1 -> curr + m
        # (n * s) - n, m ?
        # s + (n//2) * m -> we don't need to use the whole
        # we should remove 1th -> (n//2) - 1
        # s + (n // 2) * m - (n // 2) - 1
        # (n // 2) -> k

        if n == 1:
            return s

        k = n // 2
        return s + k * m - (k - 1)


s = Solution()
print(s.maximumValue(13729698, 224423547, 9083))

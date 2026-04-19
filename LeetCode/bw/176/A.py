class Solution:
    def mapWordWeights(self, words: list[str], weights: list[int]) -> str:
        st = 0
        res = ""
        for w in words:
            wl = len(w)
            s = 0
            for ch in w:
                s += weights[ord(ch) - ord("a")]
            s = s % 26
            res += chr(ord("z") - s)
            st += wl

        return res


print(
    Solution().mapWordWeights(
        ["abc", "bcd"],
        [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            25,
            26,
        ],
    )
)

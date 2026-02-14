class Solution:
    def mapWordWeights(self, words: list[str], weights: list[int]) -> str:
        st = 0
        res = ""
        for w in words:
            wl = len(w)
            s = 0
            for ch in w:
                s += weights[ord(ch) - ord('a')]
            s = s % 26
            res += chr(ord('z') - s)
            st += wl

        return res
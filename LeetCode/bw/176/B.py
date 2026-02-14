class Solution:
    def prefixConnected(self, words: list[str], k: int) -> int:
        prfx: dict[str, int] = {}
        for w in words:
            if len(w) < k:
                continue
            if w[:k] in prfx:
                prfx[w[:k]] += 1
            else:
                prfx[w[:k]] = 1

        res = 0
        for v in prfx.values():
            if v >= 2:
                res += 1

        return res

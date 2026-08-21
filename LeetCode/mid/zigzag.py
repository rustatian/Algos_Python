# https://leetcode.com/problems/zigzag-conversion


class Solution:
    # noinspection PyMethodMayBeStatic
    def convert(self, st: str, num_rows: int) -> str:
        if num_rows == 1 or len(st) == 1:
            return st
        res: list[list[str]] = [[] for _ in range(num_rows)]
        curr_row = 0
        downup = False

        for ch in st:
            res[curr_row].append(ch)
            if curr_row == 0 or curr_row == num_rows - 1:
                downup = not downup
            curr_row += 1 if downup else -1

        return "".join("".join(row) for row in res)


s = Solution()
assert s.convert("PAYPALISHIRING", 3) == "PAHNAPLSIIGYIR"
assert s.convert("PAYPALISHIRING", 4) == "PINALSIGYAHRPI"
assert s.convert("A", 1) == "A"

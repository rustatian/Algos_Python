class Solution:
    # noinspection PyMethodMayBeStatic
    def findPeakGrid(self, mat: list[list[int]]) -> list[int]:
        start_col = 0
        end_col = len(mat[0]) - 1

        while start_col <= end_col:
            mr = 0
            mcol = (start_col + end_col) // 2

            for row in range(len(mat)):
                if mat[row][mcol] > mat[mr][mcol]:
                    mr = row

            if mat[mr][mcol + 1] > mat[mr][mcol]:
                start_col = mcol + 1
                continue

            if mat[mr][mcol - 1] > mat[mr][mcol]:
                end_col = mcol - 1
                continue

            return [mr, mcol]

        return []


s = Solution()
assert s.findPeakGrid([[1, 4], [3, 2]]) == [1, 0]
assert s.findPeakGrid([[10, 20, 15], [21, 30, 14], [7, 16, 32]]) == [1, 1]

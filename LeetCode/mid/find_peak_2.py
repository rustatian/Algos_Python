class Solution:
    # noinspection PyMethodMayBeStatic
    def findPeakGrid(self, mat: list[list[int]]) -> list[int]:
        start_col = 0
        end_col = len(mat[0]) - 1

        while start_col <= end_col:
            max_row = 0
            mid_col = (end_col + start_col) // 2

            for row in range(len(mat)):
                max_row = (
                    row if (mat[row][mid_col] >= mat[max_row][mid_col]) else max_row
                )

            left = (
                    mid_col - 1 >= start_col
                    and mat[max_row][mid_col - 1] > mat[max_row][mid_col]
            )
            right = (
                    mid_col + 1 <= end_col
                    and mat[max_row][mid_col + 1] > mat[max_row][mid_col]
            )
            if not left and not right:
                return [mid_col, max_row]
            elif right:
                start_col = mid_col + 1
            else:
                end_col = mid_col - 1

        return []


s = Solution()
assert s.findPeakGrid([[1, 4], [3, 2]]) == [0, 1]
assert s.findPeakGrid([[10, 20, 15], [21, 30, 14], [7, 16, 32]]) == [1, 1]

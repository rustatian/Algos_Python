# https://leetcode.com/problems/game-of-life


class Solution:
    def gameOfLife(self, board: list[list[int]]) -> None:
        r = len(board)
        c = len(board[0])

        directions = [
            (0, 1),
            (1, 1),
            (1, 0),
            (-1, 0),
            (0, -1),
            (-1, -1),
            (1, -1),
            (-1, 1),
        ]

        for cr in range(r):
            for cc in range(c):
                live_total = 0
                for d in directions:
                    mr: int = d[0]
                    mc: int = d[1]

                    if (r > cr + mr >= 0) and (c > cc + mc >= 0):
                        if abs(board[cr + mr][cc + mc]) == 1:
                            live_total += 1

                if board[cr][cc] == 1 and (live_total < 2 or live_total > 3):
                    board[cr][cc] = -1
                if board[cr][cc] == 0 and live_total == 3:
                    board[cr][cc] = 2

        for cr in range(r):
            for cc in range(c):
                if board[cr][cc] > 0:
                    board[cr][cc] = 1
                else:
                    board[cr][cc] = 0

        return None


s = Solution()
s.gameOfLife([[0, 1, 0], [0, 0, 1], [1, 1, 1], [0, 0, 0]])

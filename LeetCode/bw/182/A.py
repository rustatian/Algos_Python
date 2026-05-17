class Solution:
    def scoreValidator(self, events: list[str]) -> list[int]:
        score = [0, 0]
        while events:
            d = events.pop(0)
            if d == "W":
                score[1] += 1
                if score[1] == 10:
                    return score
            elif d == "WD":
                score[0] += 1
            elif d == "NB":
                score[0] += 1
            else:
                score[0] += int(d)

        return score


s = Solution()
# ["W","W","W","W","W","W","W","W","W","W","6"]
events = ["W", "W", "W", "W", "W", "W", "W", "W", "W", "W", "6"]
print(s.scoreValidator(events))

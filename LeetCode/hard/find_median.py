import heapq


class MedianFinder:

    def __init__(self):
        self.h = []

    # noinspection PyMethodMayBeStatic
    def add_num(self, num: int) -> None:
        # self.h.append(num)
        # self.h.sort()
        heapq.heappush(self.h, num)

    # noinspection PyMethodMayBeStatic
    def find_median(self) -> float:
        if len(self.h) % 2 == 0:
            mid = (len(self.h) - 1) // 2
            return (self.h[mid] + self.h[mid + 1]) / 2

        return self.h[(len(self.h) - 1) // 2]


# [[],[6],[],[10],[],[2],[],[6],[],[5],[],[0],[],[6],[],[3],[],[1],[],[0],[],[0],[]]
s = MedianFinder()
s.add_num(6)
assert s.find_median() == 6
s.add_num(10)
assert s.find_median() == 8
s.add_num(2)
assert s.find_median() == 6
s.add_num(6)
assert s.find_median() == 6
s.add_num(5)
assert s.find_median() == 6
s.add_num(0)
assert s.find_median() == 5.5
s.add_num(6)
assert s.find_median() == 6
s.add_num(3)
assert s.find_median() == 5.5
s.add_num(1)
assert s.find_median() == 5
s.add_num(0)
assert s.find_median() == 4
s.add_num(0)
assert s.find_median() == 3

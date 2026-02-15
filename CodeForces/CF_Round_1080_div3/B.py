import sys

if len(sys.argv) > 1:
    sys.stdin = open(sys.argv[1], "r")


def check(x: int) -> int:
    while x % 2 == 0:
        x //= 2
    return x


def solve():
    a = list(map(int, input().split()))

    m = max(a)
    if m > len(a):
        return "No"
    d = {}

    for i in a:
        if i in d:
            return "No"
        else:
            d[i] = 1

    for i, v in enumerate(a, 1):
        if check(i) != check(v):
            return "No"

    return "Yes"


t = int(input())
ans = [solve() for _ in range(t)]

for a in ans:
    print(a)

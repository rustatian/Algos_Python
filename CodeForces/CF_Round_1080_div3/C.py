import sys

if len(sys.argv) > 1:
    sys.stdin = open(sys.argv[1], "r")


def solve():
    _ = int(input())
    a = list(map(int, input().split()))
    INF = 10**9

    dp = [INF] * 7
    for v in range(1, 7):
        dp[v] = 0 if a[0] == v else 1

    for x in a[1:]:
        ndp = [INF] * 7
        for v in range(1, 7):
            add = 0 if x == v else 1
            best = INF
            for u in range(1, 7):
                if u != v and u + v != 7:
                    cand = dp[u] + add
                    if cand < best:
                        best = cand
            ndp[v] = best
        dp = ndp

    return min(dp[1:])


t = int(input())
answ = [solve() for _ in range(t)]

for value in answ:
    print(value)

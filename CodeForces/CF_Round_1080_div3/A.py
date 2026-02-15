# import sys
#
# if len(sys.argv) > 1:
#     sys.stdin = open(sys.argv[1], "r")


def solve():
    a = list(map(int, input().split()))

    if 67 in a:
        return "Yes"
    return "No"


t = int(input())
ans = [solve() for _ in range(t)]

for a in ans:
    print(a)

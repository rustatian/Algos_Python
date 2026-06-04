def feedback(secret: str, guess: str) -> list[str]:
    alph = [0] * 26
    ans = ["*"] * len(secret)

    for ch in secret:
        alph[ord(ch) - ord("a")] += 1

    for i in range(len(secret)):
        if secret[i] == guess[i]:
            idx = ord(guess[i]) - ord("a")
            alph[idx] -= 1
            ans[i] = "match"

    for i in range(len(secret)):
        if ans[i] == "match":
            continue

        idx = ord(guess[i]) - ord("a")
        if alph[idx] >= 1:
            ans[i] = "exists"
            alph[idx] -= 1
        else:
            ans[i] = "not exists"

    return ans


print(feedback("ed", "dd"))
print(feedback("apple", "pixel"))
print(feedback("aabb", "abab"))
print(feedback("xayy", "aaaz"))

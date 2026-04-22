# https://leetcode.com/problems/evaluate-reverse-polish-notation


class Solution:
    # noinspection PyMethodMayBeStatic
    def evalRPN(self, tokens: list[str]) -> int:
        stack = []
        for curr in tokens:
            if curr == "+":
                num1 = stack.pop()
                num2 = stack.pop()
                stack.append(num1 + num2)
            elif curr == "-":
                num1 = stack.pop()
                num2 = stack.pop()
                stack.append(num2 - num1)
            elif curr == "*":
                num1 = stack.pop()
                num2 = stack.pop()
                stack.append(num2 * num1)
            elif curr == "/":
                num1 = stack.pop()
                num2 = stack.pop()
                stack.append(int(num2 / num1))
            else:
                stack.append(int(curr))
        return stack.pop()


s = Solution()
assert 9 == s.evalRPN(["2", "1", "+", "3", "*"])
assert 6 == s.evalRPN(["4", "13", "5", "/", "+"])
assert 22 == s.evalRPN(
    ["10", "6", "9", "3", "+", "-11", "*", "/", "*", "17", "+", "5", "+"]
)

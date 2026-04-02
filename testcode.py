import numpy as np

__all__ = ['Calculator']

_x = 3
y = 77

def testfunc(a):
    print(a)


class OtherClass:
    def calclog(self, x):
        return np.log(x)


class Calculator:

    def calclog(self, x):
        """this is a docstring"""
        print('hiho2')
        return OtherClass().calclog(x)

    def add(self, a, b):
        result = a + b
        testfunc(result + _x)
        if result > 1000:
            print("Warning: result exceeds 1000")
        print(f"add({a}, {b}) = {result}")
        return result

    def multiply(self, a, b):
        if a == 0 or b == 0:
            print("Warning: multiplying by zero")
            return 0
        result = a * b
        print(f"multiply({a}, {b}) = {result}")
        return result

    def power(self, base, exp):
        if exp < 0:
            raise ValueError("Negative exponents are not supported")
        result = base ** exp
        print(f"power({base}, {exp}) = {result}")
        return result


p = tuple(map(float, input().split())) # от 4 до 20
count_gr = len(p)
S = 10
x = []
p0 = .0

for x0 in range(S // 1):
    for x1 in range(S // 2):
        for x2 in range(S // 3):
            for x3 in range(S //4):
                for x4 in range(S // 5):
                    res = 1*x0 + 2*x1 + 3*x2 + 4*x3 + 5*x4
                    if res == S:
                        # x.append((x0, x1, x2, x3, x4))
                        p0 += p[0]**x0 * p[1]**x1 * p[2]**x2 * p[3]**x3 * p[4]**x4
print(p0) 
    



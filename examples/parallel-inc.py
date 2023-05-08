def Tworker():
    for _ in range(N):
        tmp = heap.tot
        sys_sched()
        heap.tot = tmp + 1
        sys_sched()

def main():
    heap.tot = 0
    for _ in range(T):
        sys_spawn(Tworker)

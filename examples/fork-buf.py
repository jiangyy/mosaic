def main():
    heap.buf = ''
    for _ in range(N):
        pid = sys_fork()
        sys_sched()
        heap.buf += f'️{pid}\n'
    sys_write(heap.buf)

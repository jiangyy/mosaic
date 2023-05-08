def main():
    pid = sys_fork()
    sys_sched()  # non-deterministic context switch
    if pid == 0:
        sys_write('World\n')
    else:
        sys_write('Hello\n')

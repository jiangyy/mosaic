def Tworker(name, delta):
  for _ in range(N):
    while heap.mutex == '🔒':  # mutex_lock()
      sys_sched()
    heap.mutex = '🔒'

    while not (0 <= heap.count + delta <= 1):
      sys_sched()
      heap.mutex = '🔓'  # cond_wait()
      heap.cond.append(name)
      while name in heap.cond:  # wait
        sys_sched()
      while heap.mutex == '🔒':  # reacquire lock
        sys_sched()
      heap.mutex = '🔒'

    if heap.cond:  # cond_signal()
      t = sys_choose(heap.cond)
      heap.cond.remove(t)  # wake up anyone
    sys_sched()

    heap.count += delta  # produce or consume

    heap.mutex = '🔓'  # mutex_unlock()
    sys_sched()

def main():
  heap.mutex = '🔓'  # 🔓 or 🔒
  heap.count = 0  # filled buffer
  heap.cond = []  # condition variable's wait list
  for i in range(T_p):
    sys_spawn(Tworker, f'Tp{i}', 1)  # delta=1, producer
  for i in range(T_c):
    sys_spawn(Tworker, f'Tc{i}', -1)  # delta=-1, consumer

def main():
  sys_bwrite(0, {})
  sys_sync()

  # 1. log the write to block #B
  log = sys_bread(0)  # read log head
  for i in range(N):
    free = max(log.values(), default=0) + 1  # allocate log block
    sys_bwrite(free, f'contents for #{i+1}')
    log = log | {i+1: free}
  b = sys_crash()
  sys_write(b)
  sys_sync()

  # 2. write updated log head
  sys_bwrite(0, log)
  sys_sync()

  # 3. install transactions
  for k, v in log.items():
    content = sys_bread(v)
    sys_bwrite(k, content)
  sys_sync()

  # 4. update log head
  sys_bwrite(0, {})
  sys_sync()

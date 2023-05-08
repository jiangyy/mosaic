def main():
  # intially, file has a single block #1
  sys_bwrite('file.inode', 'i [#1]')
  sys_bwrite('used', '#1')
  sys_bwrite('#1', '#1 (old)')
  sys_sync()

  # append a block #2 to the file
  xs = [i + 1 for i in range(N)]
  xb = ['#{x}' for x in xs]

  sys_bwrite('file.inode', f'i [{xb}]')  # inode
  sys_bwrite('used', xb)  # bitmap
  for i in range(N):
    sys_bwrite(f'#{i + 1}', f'#{i + 1} (new)')  # data block
  sys_crash()  # system crash

  # display file system state at crash recovery
  inode = sys_bread('file.inode')
  used = sys_bread('used')
  sys_write(f'{inode}; used: {used} | ')
  for i in range(N):
    if f'#{i+1}' in inode:
      b = sys_bread(f'#{i+1}')
      sys_write(f'{b} ')

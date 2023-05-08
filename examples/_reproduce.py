#!/usr/bin/env python3

import subprocess
from collections import namedtuple
from pathlib import Path
import json
import datetime

# (Run experiments once to save time)
TIMEOUT = 60  # 60s
LIMIT = 0.1   # display "<0.1s" for negligible time
RUN = 1       # run = 10 in the experiments

Subject = namedtuple('Subject', 'name configs')

# Same as Table 2
SUBJECTS = [
    Subject(
        'fork-buf',
        configs=[
            'N=1',
            'N=2',
            'N=3',
        ]
    ),
    Subject(
        'cond-var',
        configs=[
            'N=1; T_p=1; T_c=1',
            'N=1; T_p=1; T_c=2',
            'N=2; T_p=1; T_c=2',
            'N=2; T_p=2; T_c=1',
        ]
    ),
    Subject(
        'xv6-log',
        configs=[
            'N=2',
            'N=4',
            'N=8',
            'N=10',
        ]
    ),
    Subject(
        'tocttou',
        configs=[
            'P=2',
            'P=3',
            'P=4',
            'P=5',
        ]
    ),
    Subject(
        'parallel-inc',
        configs=[
            'N=1; T=2',
            'N=2; T=2',
            'N=2; T=3',
            'N=3; T=3',
        ]
    ),
    Subject(
        'fs-crash',
        configs=[
            'N=2',
            'N=4',
            'N=8',
            'N=10',
        ]
    ),
]

def run(conf, src, n=RUN):
    src = conf + '\n\n' + src
    
    PROFILING = '''
import atexit, psutil, sys

def mem():
    import os
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    print(mem_info.rss, file=sys.stderr)
    
atexit.register(mem)
'''

    def run_once(src, profiling=False):
        if profiling:
            src = PROFILING + src
        p = subprocess.run(
            ['python3', 'mosaic.py', '--check', '/dev/stdin'], 
            input=src.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT
        )
        assert p.returncode == 0
        return p

    # First run, for warm up and memory statistics
    p = run_once(src, profiling=True)
    G = json.loads(p.stdout)
    V = G['vertices']
    mem = int(p.stderr) / 1024 / 1024

    size, times = len(V), []

    # Repeated runs
    for _ in range(n):
        t1 = datetime.datetime.now()
        run_once(src, profiling=False)
        t2 = datetime.datetime.now()
        runtime = (t2 - t1).total_seconds()
        times.append(runtime)
    
    return (size, mem, times)

def evaluate(subj):
    src = Path(__file__).parent \
              .glob(f'**/{subj.name}.py').__next__() \
                  .read_text()
    LOC = len(src.strip().splitlines())
    print(f'  {subj.name} ({LOC} LOC)  '.center(62, '-'))

    for conf in subj.configs:
        try:
            states, mem, ts = run(conf, src)
            avg = sum(ts) / len(ts)
            per = int(states / avg)
            if avg < LIMIT:
                time_text = f'<{LIMIT}s'
            else:
                time_text = f'{avg:.1f}s ({per} st/s)'
        except subprocess.TimeoutExpired:
            time_text = f'Timeout (>{TIMEOUT}s)'
            print(f'{conf:>20}{time_text:>42}')
        else:
            print(f'{conf:>20}{states:>10}{time_text:>20}{mem:>10.2f}MB')

for _, subj in enumerate(SUBJECTS):
    evaluate(subj)

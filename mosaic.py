#!/usr/bin/env python3

# Mosaic Emulator and Checker

import argparse
import ast
import copy
import inspect
import json
import random
from dataclasses import dataclass
from itertools import compress, product
from pathlib import Path
from typing import Callable, Generator

## 1. Mosaic system calls

### 1.1 Process, thread, and context switching

sys_fork = lambda: os.sys_fork()
sys_spawn = lambda fn, *args: os.sys_spawn(fn, *args)
sys_sched = lambda: os.sys_sched()

### 1.2 Virtual character device

sys_choose = lambda choices: os.sys_choose(choices)
sys_write = lambda *args: os.sys_write(*args)

### 1.3 Virtual block storage device

sys_bread = lambda k: os.sys_bread(k)
sys_bwrite = lambda k, v: os.sys_bwrite(k, v)
sys_sync = lambda: os.sys_sync()
sys_crash = lambda: os.sys_crash()

### 1.4 System call helpers

SYSCALLS = []

def syscall(func):  # @syscall decorator
    SYSCALLS.append(func.__name__)
    return func

## 2. Mosaic operating system emulator

### 2.1 Data structures

class Heap:
    pass  # no member: self.__dict__ is the heap

@dataclass
class Thread:
    context: Generator  # program counter, local variables, etc.
    heap: Heap  # a pointer to thread's "memory"

@dataclass
class Storage:
    persist: dict  # persisted storage state
    buf: dict  # outstanding operations yet to be persisted

### 2.2 The OperatingSystem class

class OperatingSystem:
    """An executable operating system model.

    The operating system model hosts a single Python application with a
    main() function accessible to a shared heap and 9 system calls
    (marked by the @syscall decorator). An example:

        def main():
            pid = sys_fork()
            sys_sched()  # non-deterministic context switch
            if pid == 0:
                sys_write('World')
            else:
                sys_write('Hello')

    At any moment, this model keeps tracking a set of threads and a
    "currently running" one. Each thread consists of a reference to a
    heap object (may be shared with other threads) and a private context
    (program counters, local variables, etc.). A thread context is a
    Python generator object, i.e., a stack-less coroutine [1] that can
    save the running context and yield itself.

    For applications, the keyword "yield" is reserved for system calls.
    For example, a "choose" system call [2]:

        sys_choose(['A', 'B'])

    is transpiled as yielding the string "sys_choose" followed by its
    parameters (choices):

        res = yield 'sys_choose', ['A', 'B'].

    Yield will transfer the control to the OS for system call handling
    and eventually returning a value ('A' or 'B') to the application.

    Right after transferring control to the OS by "yield", the function
    state is "frozen", where program counters and local variables are
    accessible via the generator object. Therefore, OS can serialize its
    internal state--all thread's contexts, heaps, and virtual device
    states at this moment.

    In this sense, operating system is a system-call driven state
    transition system:

        (s0) --run first thread (main)-> (s1)
             --sys_choose and application execution-> (s2)
             --sys_sched and application execution-> (s3) ...

    Real operating systems can be preemptive--context switching can
    happen non-deterministically at any program point, simply because
    processor can non-deterministically interrupt its currently running
    code and transfer the control to the operating system.

    The OS internal implementation does NOT immediately process the
    system call: it returns all possible choices available at the moment
    and their corresponding processing logic as callbacks. For the
    example above, the "choose" system call returns a non-deterministic
    choice among given choices. The internal implementation thus returns

        choices = {
            'A': (lambda: 'A'),
            'B': (lambda: 'B'),
        }

    for later processing. Another example is non-deterministic context
    switching by yielding 'sys_sched'. Suppose there are threads t1 and
    t2 at the moment. The system call handler will return

        choices = {
            't1': (lambda: switch_to(t1)),
            't2': (lambda: switch_to(t2)),
        }

    in which switch_to(th) replaces the OS's current running thread with
    th (changes the global "heap" variable). Such deferred execution of
    system calls separates the mechanism of non-deterministic choices
    from the actual decision makers (e.g., an interpreter or a model
    checker). Once the decision is made, the simply call step(choice)
    and the OS will execute this choice by

        choices[choice]()

    with the application code (generator) being resumed.

    This model provides "write" system call to immediately push data to
    a hypothetical character device like a tty associated with stdout.
    We model a block device (key-value store) that may lose data upon
    crash. The model assumes atomicity of each single block write (a
    key-value update). However, writes are merely to a volatile buffer
    which may non-deterministically lose data upon crash 3]. The "sync"
    system call persists buffered writes.

    References:

    [1] N. Schemenauer, T. Peters, and M. L. Hetland. PEP 255 -
        Simple generators. https://peps.python.org/pep-0255/
    [2] J. Yang, C. Sar, and D. Engler. eXplode: a lightweight, general
        system for finding serious storage system errors. OSDI'06.
    [3] T. S. Pillai, V. Chidambaram, R. Alagappan, A. Al-Kiswany, A. C.
        Arpaci-Dusseau, and R. H. Arpaci-Dusseau. All file systems are
        not created equal: On the complexity of crafting crash
        consistent applications. OSDI'14.
    """

    def __init__(self, init: Callable):
        """Create a new OS instance with pending-to-execute init thread."""
        # Operating system states
        self._threads = [Thread(context=init(), heap=Heap())]
        self._current = 0
        self._choices = {init.__name__: lambda: None}
        self._stdout = ''
        self._storage = Storage(persist={}, buf={})

        # Internal states
        self._init = init
        self._trace = []
        self._newfork = set()

### 2.3 System call implementation

#### 2.3.1 Process, thread, and context switching

    @syscall
    def sys_spawn(self, func: Callable, *args):
        """Spawn a heap-sharing new thread executing func(args)."""
        def do_spawn():
            self._threads.append(
                Thread(
                    context=func(*args),  # func() returns a new generator
                    heap=self.current().heap,  # shared heap
                )
            )
        return {'spawn': (lambda: do_spawn())}

    @syscall
    def sys_fork(self):
        """Create a clone of the current thread with a copied heap."""
        if all(not f.frame.f_locals['fork_child']
                for f in inspect.stack()
                    if f.function == '_step'):  # this is parent; do fork
            # Deep-copying generators causes troubles--they are twined with
            # Python's runtime state. We use an (inefficient) hack here: replay 
            # the trace and override the last fork to avoid infinite recursion.
            os_clone = OperatingSystem(self._init)
            os_clone.replay(self._trace[:-1])
            os_clone._step(self._trace[-1], fork_child=True)

            # Now os_clone._current is the forked process. Cloned thread just 
            # yields a sys_fork and is pending for fork()'s return value. It
            # is necessary to mark cloned threads (in self._newfork) and send
            # child's fork() return value when they are scheduled for the
            # first time.
            def do_fork():
                self._threads.append(os_clone.current())
                self._newfork.add((pid := len(self._threads)) - 1)
                return 1000 + pid  # returned pid starts from 1000

            return {'fork': (lambda: do_fork())}
        else:
            return None  # overridden fork; this value is never used because
                         # os_clone is destroyed immediately after fork()

    @syscall
    def sys_sched(self):
        """Return a non-deterministic context switch to a runnable thread."""
        return {
            f't{i+1}': (lambda i=i: self._switch_to(i))
                for i, th in enumerate(self._threads)
                    if th.context.gi_frame is not None  # thread still alive?
        }

### 2.3.2 Virtual character device (byte stream)

    @syscall
    def sys_choose(self, choices):
        """Return a non-deterministic value from choices."""
        return {f'choose {c}': (lambda c=c: c) for c in choices}

    @syscall
    def sys_write(self, *args):
        """Write strings (space separated) to stdout."""
        def do_write():
            self._stdout += ' '.join(str(arg) for arg in args)
        return {'write': (lambda: do_write())}

### 2.3.3 Virtual block storage device

    @syscall
    def sys_bread(self, key):
        """Return the specific key's associated value in block device."""
        storage = self._storage
        return {'bread': (lambda:
            storage.buf.get(key,  # always try to read from buffer first
                storage.persist.get(key, None)  # and then persistent storage
            )
        )}

    @syscall
    def sys_bwrite(self, key, value):
        """Write (key, value) pair to block device's buffer."""
        def do_bwrite():
            self._storage.buf[key] = value
        return {'bwrite': (lambda: do_bwrite())}

    @syscall
    def sys_sync(self):
        """Persist all buffered writes."""
        def do_sync():
            store = self._storage
            self._storage = Storage(
                persist=store.persist | store.buf,  # write back
                buf={}
            )
        return {'sync': (lambda: do_sync())}

    @syscall
    def sys_crash(self):
        """Simulate a system crash that non-deterministically persists
        outstanding writes in the buffer.
        """
        persist = self._storage.persist
        btrace = self._storage.buf.items()  # block trace

        crash_sites = (
            lambda subset=subset:
                setattr(self, '_storage',
                    Storage(  # persist only writes in the subset
                        persist=persist | dict(compress(btrace, subset)),
                        buf={}
                    )
                ) for subset in  # Mosaic allows persisting any subset of
                    product(     # pending blocks in the buffer
                        *([(0, 1)] * len(btrace))
                    )
        )
        return dict(enumerate(crash_sites))

### 2.4 Operating system as a state machine

    def replay(self, trace: list) -> dict:
        """Replay an execution trace and return the resulting state."""
        for choice in trace:
            self._step(choice)
        return self.state_dump()

    def _step(self, choice, fork_child=False):
        self._switch_to(self._current)
        self._trace.append(choice)  # keep all choices for replay-based fork()
        action = self._choices[choice]  # return value of sys_xxx: a lambda
        res = action()

        try:  # Execute current thread for one step
            func, args = self.current().context.send(res)
            assert func in SYSCALLS
            self._choices = getattr(self, func)(*args)
        except StopIteration:  # ... and thread terminates
            self._choices = self.sys_sched()

        # At this point, the operating system's state is
        #   (self._threads, self._current, self._stdout, self._storage)
        # and outgoing transitions are saved in self._choices.

### 2.5 Misc and helper functions

    def state_dump(self) -> dict:
        """Create a serializable Mosaic state dump with hash code."""
        heaps = {}
        for th in self._threads:
            if (i := id(th.heap)) not in heaps:  # unique heaps
                heaps[i] = len(heaps) + 1

        os_state = {
            'current': self._current,
            'choices': sorted(list(self._choices.keys())),
            'contexts': [
                {
                    'name': th.context.gi_frame.f_code.co_name,
                    'heap': heaps[id(th.heap)],  # the unique heap id
                    'pc': th.context.gi_frame.f_lineno,
                    'locals': th.context.gi_frame.f_locals,
                } if th.context.gi_frame is not None else None
                    for th in self._threads
            ],
            'heaps': {
                heaps[id(th.heap)]: th.heap.__dict__
                    for th in self._threads
            },
            'stdout': self._stdout,
            'store_persist': self._storage.persist,
            'store_buffer': self._storage.buf,
        }

        h = hash(json.dumps(os_state, sort_keys=True)) + 2**63
        return (copy.deepcopy(os_state)  # freeze the runtime state
                | dict(hashcode=f'{h:016x}'))

    def current(self) -> Thread:
        """Return the current running thread object."""
        return self._threads[self._current]

    def _switch_to(self, tid: int):
        self._current = tid
        globals()['os'] = self
        globals()['heap'] = self.current().heap
        if tid in self._newfork:
            self._newfork.remove(tid)  # tricky: forked process must receive 0
            return 0                   # to indicate a child

## 3. The Mosaic runtime

class Mosaic:
    """The operating system interpreter and model checker.

    The operating system model is a state transition system: os.replay()
    maps any trace to a state (with its outgoing transitions). Based
    on this model, two state space explorers are implemented:

    - run:   Choose outgoing transitions uniformly at random, yielding a
             single execution trace.
    - check: Exhaustively explore all reachable states by a breadth-
             first search. Duplicated states are not visited twice.

    Both explorers produce the visited portion of the state space as a
    serializable object containing:

    - source:   The application source code
    - vertices: A list of operating system state dumps. The first vertex
                in the list is the initial state. Each vertex has a
                unique "hashcode" id.
    - edges:    A list of 3-tuples: (source, target, label) denoting an 
                explored source --[label]-> target edge. Both source and
                target are state hashcode ids.
    """

### 3.1 Model interpreter and checker

    def run(self) -> dict:
        """Interpret the model with non-deterministic choices."""
        os = OperatingSystem(self.entry)
        V, E = [os.state_dump() | dict(depth=0)], []

        while (choices := V[-1]['choices']):
            choice = random.choice(choices)  # uniformly at random
            V.append(os.replay([choice]) | dict(depth=len(V)))
            E.append((V[-2]['hashcode'], V[-1]['hashcode'], choice))

        return dict(source=self.src, vertices=V, edges=E)

    def check(self) -> dict:
        """Exhaustively explore the state space."""
        class State:
            entry = self.entry

            def __init__(self, trace):
                self.trace = trace
                self.state = OperatingSystem(State.entry).replay(trace)
                self.state |= dict(depth=0)
                self.hashcode = self.state['hashcode']

            def extend(self, c):
                st = State(self.trace + (c,))
                st.state = st.state | dict(depth=self.state['depth'] + 1)
                return st

        st0 = State(tuple())  # initial state of empty trace
        queued, V, E = [st0], {st0.hashcode: st0.state}, []

        while queued:
            st = queued.pop(0)
            for choice in st.state['choices']:
                st1 = st.extend(choice)
                if st1.hashcode not in V:  # found an unexplored state
                    V[st1.hashcode] = st1.state
                    queued.append(st1)
                E.append((st.hashcode, st1.hashcode, choice))

        return dict(
            source=self.src,
            vertices=sorted(V.values(), key=lambda st: st['depth']),
            edges=E
        )

### 3.1 Source code parsing and rewriting

    class Transformer(ast.NodeTransformer):
        def visit_Call(self, node):
            # Rewrite system calls as yields
            if (isinstance(node.func, ast.Name) and
                    node.func.id in SYSCALLS):  # rewrite system calls
                return ast.Yield(ast.Tuple(     #   -> yield ('sys_xxx', args)
                    elts=[
                        ast.Constant(value=node.func.id),
                        ast.Tuple(elts=node.args),
                    ]
                ))
            else:
                return node

    def __init__(self, src: str):
        tree = ast.parse(src)
        hacked_ast = self.Transformer().visit(tree)
        hacked_src = ast.unparse(hacked_ast)

        context = {}
        exec(hacked_src, globals(), context)
        globals().update(context)

        self.src = src
        self.entry = context['main']  # must have a main()

## 4. Utilities

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='The modeled operating system and state explorer.'
    )
    parser.add_argument(
        'source',
        help='application code (.py) to be checked; must have a main()'
    )
    parser.add_argument('-r', '--run', action='store_true')
    parser.add_argument('-c', '--check', action='store_true')
    args = parser.parse_args()

    src = Path(args.source).read_text()
    mosaic = Mosaic(src)
    if args.check:
        explored = mosaic.check()
    else:
        explored = mosaic.run()  # run is the default option

    # Serialize the explored states and write to stdout. This encourages piping
    # the results to another tool following the UNIX philosophy. Examples:
    #
    #   mosaic --run foo.py | grep stdout | tail -n 1  # quick and dirty check
    #   mosaic --check bar.py | fx  # or any other interactive visualizer
    #
    print(json.dumps(explored, ensure_ascii=False, indent=2))

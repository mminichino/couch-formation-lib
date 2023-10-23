##
##

import logging
import concurrent.futures
from multiprocessing import get_context
from couchformation.executor.taskqueue import TaskQueue

logger = logging.getLogger('couchformation.executor.dispatch')
logger.addHandler(logging.NullHandler())


def run(module, instance, method, *args, **kwargs):
    m = __import__(module, fromlist=[""])
    i = getattr(m, instance)
    obj = i(*args, **kwargs)
    f = getattr(obj, method)
    f()


class JobDispatch(object):

    def __init__(self):
        context = get_context('fork')
        self.executor = concurrent.futures.ProcessPoolExecutor(mp_context=context)
        self.tasks = set()

    def dispatch(self, *args, **kwargs):
        self.tasks.add(self.executor.submit(run, *args, **kwargs))

    def join(self):
        cmd_failed = False
        while self.tasks:
            done, self.tasks = concurrent.futures.wait(self.tasks, return_when=concurrent.futures.FIRST_COMPLETED)
            for task in done:
                try:
                    res = task.result()
                except Exception as err:
                    raise RuntimeError(err)
        if cmd_failed:
            raise RuntimeError("command returned non-zero result")

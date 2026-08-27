"""进程内并发执行器；持久化任务仍由 web.tasks/worker 负责。"""

from concurrent.futures import ThreadPoolExecutor, as_completed


class AgentQueue:
    def __init__(self, max_workers=4):
        self.max_workers = max(1, int(max_workers))

    def run(self, jobs, timeout=None):
        results = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(jobs) or 1)) as pool:
            futures = {pool.submit(fn): i for i, fn in enumerate(jobs)}
            for future in as_completed(futures, timeout=timeout):
                results[futures[future]] = future.result()
        return results

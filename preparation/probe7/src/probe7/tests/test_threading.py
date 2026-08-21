from probe7.src import LoadBalancer
import threading


def test_threading():
    lb = LoadBalancer(capacity=10)
    l = threading.Lock()
    fails = []

    def worker(i: int):
        try:
            lb.register(f"server_{i}")
            lb.unregister(f"server_{i}")
        except Exception as e:
            with l:
                fails.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

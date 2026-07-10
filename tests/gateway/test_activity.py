import threading

from gateway.activity import active_count, activity, begin_activity, end_activity


def test_activity_registry_counts_worker_types_and_cleanup():
    assert active_count() == 0
    chat = begin_activity("chat", "session-1")
    cron = begin_activity("cron", "job-1")
    api_run = begin_activity("api_run", "run-1")
    assert active_count() == 3

    end_activity(chat)
    end_activity(cron)
    end_activity(api_run)
    end_activity(api_run)  # release is deliberately idempotent
    assert active_count() == 0


def test_activity_registry_is_visible_across_threads():
    entered = threading.Event()
    release = threading.Event()

    def worker():
        with activity("cron", "job-thread"):
            entered.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(timeout=2)
    assert active_count() == 1
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert active_count() == 0

"""
Background scheduler: auto-releases escrowed payment to the artisan if the
client hasn't approved/disputed a 'pending_review' job within 24 hours.

Uses APScheduler running inside the same process as Flask. This is fine for
a single-process dev server (e.g. `python main.py` or `flask run`), but if
you later deploy with multiple worker processes (e.g. gunicorn -w 4), this
job would run once per worker and could double-pay. In that case, move this
to a separate one-off process/cron instead of starting it from create_app().
"""
from apscheduler.schedulers.background import BackgroundScheduler
from database_helper import get_db
from utils import release_job_payment

_scheduler = None


def _auto_release_overdue_jobs():
    """Find jobs stuck in pending_review past their review_deadline and pay out."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT * FROM jobs
               WHERE status = 'verified'
                 AND paid_at IS NULL
                 AND review_deadline IS NOT NULL
                 AND review_deadline <= NOW()"""
        )
        overdue_jobs = cur.fetchall()

        for job in overdue_jobs:
            try:
                ref = release_job_payment(job, cur)
                conn.commit()
                if ref is None:
                    print(f"[auto-release] Job {job['id']} already resolved by someone else — skipped.")
                else:
                    print(f"[auto-release] Job {job['id']} auto-paid after 24h (ref={ref})")
            except Exception as e:
                conn.rollback()
                print(f"[auto-release] Failed for job {job['id']}: {e}")
    except Exception as e:
        print(f"[auto-release] Query error: {e}")
    finally:
        cur.close()
        conn.close()


def start_scheduler():
    """Call this once from create_app(). Safe to call multiple times (no-op after first)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    # Check every 10 minutes — frequent enough that nothing waits much past 24h,
    # cheap enough not to hammer the DB.
    _scheduler.add_job(_auto_release_overdue_jobs, "interval", minutes=10, id="auto_release_overdue_jobs")
    _scheduler.start()
    print("[scheduler] Auto-release scheduler started (checks every 10 min)")
    return _scheduler
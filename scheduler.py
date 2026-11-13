"""Background jobs (run in ONE process - start gunicorn with -w 1 --threads 8):
 1. auto-release: credit the artisan's wallet if the client ignored a verified job for 24h
 2. payout requery: settle withdrawals Squad left pending/unknown (success or refund)
 3. payment reconcile: catch escrow payments whose webhook never arrived
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from database_helper import get_db
import squad_service
import push
from utils import release_job_payment, release_gig_slot_payment, refund_payout, mark_payout_success
from routes.webhook import mark_escrow_paid

_scheduler = None


def _db_job(fn):
    def run():
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        try:
            fn(conn, cur)
        except Exception as e:
            conn.rollback()
            print(f"[scheduler] {fn.__name__} error: {e}")
        finally:
            cur.close()
            conn.close()
    run.__name__ = fn.__name__
    return run


@_db_job
def auto_release(conn, cur):
    for table, fn in (("jobs", release_job_payment), ("job_workers", release_gig_slot_payment)):
        cur.execute(f"""SELECT * FROM {table} WHERE status='verified' AND paid_at IS NULL
                        AND review_deadline IS NOT NULL AND review_deadline <= NOW()""")
        for row in cur.fetchall():
            try:
                ref = fn(row, cur)
                conn.commit()
                print(f"[auto-release] {table} {row['id']} -> {ref}")
                if ref and table == "jobs":
                    push.spawn(push.notify_released, row["id"], True)
            except Exception as e:
                conn.rollback()
                print(f"[auto-release] {table} {row['id']} failed: {e}")


@_db_job
def requery_payouts(conn, cur):
    cur.execute("""SELECT id, reference, created_at FROM payouts
                   WHERE status IN ('processing','pending') AND created_at <= NOW() - INTERVAL 1 MINUTE
                   ORDER BY id LIMIT 50""")
    for p in cur.fetchall():
        r = squad_service.requery_transfer(p["reference"])
        if r["state"] == "success":
            mark_payout_success(cur, p["id"], r["message"])
        elif r["state"] == "failed":
            refund_payout(cur, p["id"], r["message"])
        else:
            cur.execute("""UPDATE payouts SET status='needs_review', updated_at=NOW()
                           WHERE id=%s AND created_at <= NOW() - INTERVAL 2 HOUR""", (p["id"],))
            if cur.rowcount:
                print(f"[payout] #{p['id']} needs manual review in the Squad dashboard (ref {p['reference']})")
        conn.commit()


@_db_job
def reconcile_payments(conn, cur):
    for table in ("jobs", "job_workers"):
        cur.execute(f"""SELECT escrow_reference FROM {table}
                        WHERE escrow_paid=0 AND escrow_reference IS NOT NULL
                        ORDER BY id DESC LIMIT 40""")
        for row in cur.fetchall():
            v = squad_service.verify_payment(row["escrow_reference"])
            if v["ok"] and v["paid"]:
                print(f"[reconcile] {row['escrow_reference']} -> {mark_escrow_paid(conn, cur, row['escrow_reference'], v['amount_kobo'] or 0)}")


def start_scheduler():
    global _scheduler
    if _scheduler is not None or os.getenv("RUN_SCHEDULER", "1") != "1":
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    kw = dict(max_instances=1, coalesce=True)
    _scheduler.add_job(auto_release,        "interval", minutes=10, id="auto_release", **kw)
    _scheduler.add_job(requery_payouts,     "interval", minutes=2,  id="requery_payouts", **kw)
    _scheduler.add_job(reconcile_payments,  "interval", minutes=5,  id="reconcile_payments", **kw)
    _scheduler.start()
    print("[scheduler] started")
    return _scheduler

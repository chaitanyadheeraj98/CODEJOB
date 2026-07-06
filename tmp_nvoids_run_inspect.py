import json
import sqlite3


def main() -> None:
    conn = sqlite3.connect("/app/data/codejob.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    run = cur.execute(
        """
        select id, started_at, ended_at, fetched_count, created_count, deduped_count, failed_count, notes
        from external_scrape_runs
        where source_type='nvoids'
        order by id desc
        limit 1
        """
    ).fetchone()
    if run is None:
        print(json.dumps({"error": "no_nvoids_run"}))
        return
    run_dict = dict(run)
    print(json.dumps({"run": run_dict}, indent=2))
    rows = cur.execute(
        """
        select id, external_post_id, role, recruiter_email, recruiter_phone, location, bridge_status, created_at
        from external_opportunities
        where source_type='nvoids'
          and created_at between ? and ?
        order by created_at asc
        """,
        (run["started_at"], run["ended_at"]),
    ).fetchall()
    print(json.dumps({"rows": [dict(r) for r in rows]}, indent=2))


if __name__ == "__main__":
    main()

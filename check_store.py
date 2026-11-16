"""Quick check for store.py. Uses a throwaway in-memory database, so nothing is saved."""
from store import connect, latest_version, save

conn = connect(":memory:")

print("1. Unknown load ->", latest_version(conn, "MFG-33018"))

v1 = save(conn, "MFG-33018", "2026-11-05", "rc6a.txt", "hashA",
          {"rate": {"total": 1650}}, [], [], "ok")
print("2. First save is version", v1)

v2 = save(conn, "MFG-33018", "2026-11-06", "rc6b.txt", "hashB",
          {"rate": {"total": 1950}}, [], ["rate.total: 1650 -> 1950"], "revision")
print("3. Second save is version", v2)

latest = latest_version(conn, "MFG-33018")
print("4. Latest version:", latest["version"], "| total:", latest["data"]["rate"]["total"],
      "| changes:", latest["changes"])

count = conn.execute("SELECT COUNT(*) FROM rate_cons").fetchone()[0]
print("5. Rows stored:", count)

expected = (v1 == 1 and v2 == 2 and latest["version"] == 2
            and latest["data"]["rate"]["total"] == 1950 and count == 2)
print("\nALL GOOD" if expected else "\nSomething's off - compare with the expected output")

revisions = conn.execute("SELECT COUNT(*) FROM rate_cons WHERE status = 'revision'").fetchone()[0]
print("6. Revisions:", revisions)

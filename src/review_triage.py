"""Sort the pending MusicBrainz review queue (data/reviewed_candidates.json).

Nothing is imported (project rule: no automatic import of MusicBrainz
candidates).  Each pending release group gets one of:
  catalogued   a release of that title is already in the catalogue (often a
               bootleg from the owner's library) -> excluded with a note
  guest        Lil Wayne is not the lead artist ("X feat. Lil Wayne",
               "X & Lil Wayne"): such songs are kept as guest songs only
               (owner's decision, Nov 13, 2026) -> excluded with a note
  owner        everything else: listed for the owner (title, type, date, credit)
The artist credit comes from MusicBrainz (1 request per second, cached in
exports/review_credits.json).

usage:
    python review_triage.py            # dry run: counts and the owner's list
    python review_triage.py --apply    # write the exclusions into the review queue
"""

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

from import_release import DATABASE_PATH
from musicbrainz_catalog import BASE_URL, api_request
from streaming_search import base_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = PROJECT_ROOT / "data" / "reviewed_candidates.json"
CREDITS_PATH = PROJECT_ROOT / "exports" / "review_credits.json"
LEAD = re.compile(r"^(lil'? wayne|lil’ wayne|weezy|tunechi)\b", re.IGNORECASE)


def artist_credit(group_id, cache):
    if group_id not in cache:
        data = None
        for attempt in range(1, 4):
            time.sleep(1 if attempt == 1 else 3 * attempt)  # rate limit; 503 = busy
            data = api_request(f"{BASE_URL}release-group/{group_id}?inc=artist-credits&fmt=json")
            if data is not None:
                break
        if data is None:
            return None
        cache[group_id] = "".join(c.get("name", "") + c.get("joinphrase", "") for c in data.get("artist-credit") or [])
        CREDITS_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return cache[group_id]


def sort_queue(candidates, catalogued_titles, cache):
    """[(candidate, kind, detail)] for the pending candidates."""
    result = []
    for candidate in candidates:
        if candidate.get("decision") != "pending":
            continue
        key = base_key(candidate["title"])
        if key in catalogued_titles:
            result.append((candidate, "catalogued", catalogued_titles[key]))
            continue
        credit = artist_credit(candidate["release_group_id"], cache)
        if credit is not None and not LEAD.match(credit.strip()):
            result.append((candidate, "guest", credit))
        else:
            result.append((candidate, "owner", credit))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sort the pending MusicBrainz review queue.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    try:
        catalogued = {}
        for title, discography in connection.execute("SELECT title, discography FROM releases"):
            catalogued.setdefault(base_key(title), f"{title} ({discography})")
    finally:
        connection.close()
    cache = json.loads(CREDITS_PATH.read_text(encoding="utf-8")) if CREDITS_PATH.exists() else {}
    sorted_queue = sort_queue(queue["candidates"], catalogued, cache)
    counts = {}
    for candidate, kind, detail in sorted_queue:
        counts[kind] = counts.get(kind, 0) + 1
    print(counts)
    print("\nFor the owner:")
    for candidate, kind, detail in sorted_queue:
        if kind == "owner":
            types = "/".join([candidate.get("musicbrainz_primary_type") or "?"] + list(candidate.get("musicbrainz_secondary_types") or []))
            print(f"  {candidate.get('first_release_date') or '?':<10} {types:<28} {candidate['title'][:55]:<55} {detail or '?'}")
    if not args.apply:
        print("\nDry run; --apply writes the exclusions (catalogued, guest) into the review queue.")
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    for candidate, kind, detail in sorted_queue:
        if kind == "catalogued":
            candidate.update(decision="excluded", reviewed_at=now,
                             review_note=f"Already in the catalogue as '{detail}' (review_triage.py, Nov 15, 2026).")
        elif kind == "guest":
            candidate.update(decision="excluded", reviewed_at=now,
                             review_note=f"Lead artist '{detail}': a guest appearance, kept as guest songs only "
                                         "(owner's decision Nov 13, 2026; review_triage.py).")
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Exclusions written.")


if __name__ == "__main__":
    main()

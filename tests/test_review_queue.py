import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import database
import review_queue
import song_review
import tracker_server


SURVEY_SONGS = [
    {"title": "Downtown", "main_artist": "2 Chainz", "sources": "genius", "in_catalogue": [1], "group_song": False,
     "musicbrainz": [], "genius": [{"id": 50, "url": "https://genius.com/downtown", "release_date": None}],
     "hq": False, "hq_projects": []},
    {"title": "Rock Star", "main_artist": "Chamillionaire", "sources": "genius", "in_catalogue": [2],
     "group_song": False, "musicbrainz": [], "genius": [{"id": 51, "url": "https://genius.com/rs", "release_date": None}],
     "hq": False, "hq_projects": []},
]


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.db = root / "test.db"
        self.decisions = root / "decisions.json"
        exports = root / "exports"
        exports.mkdir()
        (exports / "guest_survey_20990101.json").write_text(json.dumps({"songs": SURVEY_SONGS}), encoding="utf-8")
        (exports / "guest_matches_20990101.json").write_text(json.dumps({"songs": [
            {"title": s["title"], "main_artist": s["main_artist"], "sources": s["sources"], "verdict": "review",
             "evidence": [], "candidates": s["in_catalogue"]} for s in SURVEY_SONGS]}), encoding="utf-8")
        self._saved = (database.DATABASE_PATH, review_queue.EXPORTS_FOLDER, song_review.EXPORTS_FOLDER)
        database.DATABASE_PATH = self.db
        review_queue.EXPORTS_FOLDER = song_review.EXPORTS_FOLDER = exports
        song_review._SURVEY.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            database.initialize_database()
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO recordings (id, title, artist_id) VALUES (1, 'Downtown', 1), (2, 'Rock Star', 1)")
        c.commit()
        c.close()

    def tearDown(self):
        database.DATABASE_PATH, review_queue.EXPORTS_FOLDER, song_review.EXPORTS_FOLDER = self._saved
        song_review._SURVEY.clear()
        self._directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def test_items_and_decisions(self):
        connection = self.connect()
        items = review_queue.review_items(connection, {}, review_queue.load_decisions(self.decisions))
        self.assertEqual([i["key"] for i in items], ["guest:2 Chainz|Downtown", "guest:Chamillionaire|Rock Star"])
        with self.assertRaises(ValueError):
            review_queue.record_decision({"key": items[0]["key"], "action": "guest_same", "recording": 99},
                                         items, self.decisions)
        with self.assertRaises(ValueError):
            review_queue.record_decision({"key": "guest:nope", "action": "skip"}, items, self.decisions)
        review_queue.record_decision({"key": items[0]["key"], "action": "guest_same", "recording": 1},
                                     items, self.decisions)
        review_queue.record_decision({"key": items[1]["key"], "action": "guest_different"}, items, self.decisions)
        decisions = review_queue.load_decisions(self.decisions)
        self.assertEqual(review_queue.review_items(connection, {}, decisions), [])
        # song_review applies the web decisions.
        planned = song_review.plan(connection, decisions["decisions"])
        self.assertEqual([status for _, status in planned], ["apply", "apply"])
        with connection:
            song_review.apply(connection, planned)
        self.assertEqual(connection.execute("SELECT genius_song_id FROM recordings WHERE id = 1").fetchone()[0], 50)
        guest = connection.execute("SELECT a.name FROM recordings r JOIN artists a ON a.id = r.artist_id "
                                   "WHERE r.appearance = 'guest' AND r.title = 'Rock Star'").fetchall()
        self.assertEqual(guest, [("Chamillionaire",)])
        self.assertEqual([s for _, s in song_review.plan(connection, decisions["decisions"])][1], "done")
        connection.close()

    def test_server_post(self):
        tracker_server.Handler.database_path = self.db
        tracker_server.Handler.decisions_path = self.decisions
        server = ThreadingHTTPServer(("127.0.0.1", 0), tracker_server.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def post(body, headers=None):
                request = urllib.request.Request(base + "/api/review", data=json.dumps(body).encode(), method="POST",
                                                 headers={"Content-Type": "application/json", **(headers or {})})
                return urllib.request.urlopen(request, timeout=10)
            with urllib.request.urlopen(base + "/api/review", timeout=10) as response:
                self.assertEqual(len(json.loads(response.read())), 2)
            with post({"key": "guest:2 Chainz|Downtown", "action": "skip"}) as response:
                self.assertEqual(json.loads(response.read())["action"], "skip")
            for body, headers, code in (({"key": "guest:2 Chainz|Downtown", "action": "skip"}, None, 400),
                                        ({"key": "guest:Chamillionaire|Rock Star", "action": "drop table"}, None, 400),
                                        ({"key": "guest:Chamillionaire|Rock Star", "action": "skip"},
                                         {"Origin": "http://evil.example"}, 403)):
                with self.subTest(body=body):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post(body, headers)
                    self.assertEqual(error.exception.code, code)
            self.assertEqual(len(review_queue.load_decisions(self.decisions)["decisions"]), 1)
        finally:
            server.shutdown()
            server.server_close()
            tracker_server.Handler.decisions_path = None


if __name__ == "__main__":
    unittest.main()

"""Lil Wayne's profile for the tracker's About page (data/artist_profile.json).

Read-only for SQLite.  Collects
  * the official links of the MusicBrainz artist (social networks, streaming,
    official homepage, reference sites); dead services (Google+, Myspace) are
    left out;
  * the first sentences of the English Wikipedia article (found via
    MusicBrainz -> Wikidata -> enwiki; CC BY-SA, URL kept as attribution);
  * the MusicBrainz "image" (a Wikimedia Commons file, free licence): a
    thumbnail saved to web/covers/artist.jpg with author and licence.

usage:
    python artist_profile.py
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import era_info
import wikipedia_source
from musicbrainz_catalog import BASE_URL, HEADERS, api_request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "data" / "artist_profile.json"
PHOTO_PATH = PROJECT_ROOT / "web" / "covers" / "artist.jpg"
LIL_WAYNE_MBID = "ac9a487a-d9d2-4f27-bb23-0f4686488345"
DEAD = re.compile(r"plus\.google\.com|myspace\.com|napster\.com", re.IGNORECASE)
# Not his: a fan site and an expired promo domain listed as "official homepage", and the
# pages of his songwriter name "Dwayne Carter" (Discogs 689691; AllMusic mn0000665541,
# named so on Wikidata Q15615 P1728) next to his artist pages (owner's request, Nov 15, 2026).
DOUBTFUL = re.compile(r"lilwayne-online\.com|thacarterv\.com|discogs\.com/artist/689691\b|allmusic\.com/artist/mn0000665541\b",
                      re.IGNORECASE)
GROUPS = (
    ("social", ("social network", "youtube")),
    ("listen", ("free streaming", "streaming", "youtube music", "purchase for download", "soundcloud")),
    ("official", ("official homepage",)),
    ("reference", ("lyrics", "discogs", "allmusic", "setlistfm", "wikidata", "IMDb", "last.fm")),
)
LISTEN_SERVICES = re.compile(r"spotify|music\.apple|tidal|deezer|music\.amazon|music\.youtube|soundcloud", re.IGNORECASE)


def musicbrainz_artist():
    for attempt in range(3):
        time.sleep(1 + 2 * attempt)
        data = api_request(f"{BASE_URL}artist/{LIL_WAYNE_MBID}?inc=url-rels&fmt=json")
        if data:
            return data
    raise RuntimeError("MusicBrainz artist could not be retrieved.")


def grouped_links(relations):
    """{group: [{type, url}]}, one link per host for listen/social (the first one)."""
    result, hosts = {name: [] for name, _ in GROUPS}, set()
    for relation in relations:
        url, kind = (relation.get("url") or {}).get("resource", ""), relation.get("type", "")
        if not url or DEAD.search(url) or DOUBTFUL.search(url):
            continue
        for name, kinds in GROUPS:
            if kind in kinds:
                if name == "listen" and not LISTEN_SERVICES.search(url):
                    break
                host = urllib.parse.urlparse(url).netloc.replace("www.", "").replace("itunes.apple", "music.apple")
                if name in ("listen", "social") and host in hosts:
                    break
                hosts.add(host)
                result[name].append({"type": kind, "url": url.replace("http://", "https://")})
                break
    return result


def commons_photo(file_url):
    """(thumbnail bytes, {author, licence, page}) of a Wikimedia Commons file, or (None, None)."""
    title = urllib.parse.unquote(file_url.rsplit("/", 1)[-1])
    query = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|extmetadata",
                                    "iiurlwidth": 600, "format": "json", "formatversion": 2})
    request = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + query, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        page = json.loads(response.read().decode("utf-8"))["query"]["pages"][0]
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    strip = lambda html: re.sub(r"<[^>]+>", "", (html or {}).get("value", "")).strip()
    thumb = info.get("thumburl")
    if not thumb:
        return None, None
    with urllib.request.urlopen(urllib.request.Request(thumb, headers=HEADERS), timeout=60) as response:
        data = response.read()
    return data, {"author": strip(meta.get("Artist")), "licence": strip(meta.get("LicenseShortName")),
                  "page": info.get("descriptionurl")}


def main():
    artist = musicbrainz_artist()
    relations = artist.get("relations", [])
    qid = next((re.search(r"(Q\d+)", r["url"]["resource"]).group(1) for r in relations if r.get("type") == "wikidata"), None)
    title = era_info.enwiki_title(qid) if qid else None
    summary, summary_url = era_info.wikipedia_summary(title) if title else (None, None)
    if title:  # a longer intro for the About page (6 sentences)
        data = wikipedia_source.api_get({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                         "redirects": 1, "titles": title}) or {}
        pages = data.get("query", {}).get("pages", [])
        if pages and pages[0].get("extract"):
            summary = era_info.first_sentences(pages[0]["extract"], count=6)
    photo = None
    image = next((r["url"]["resource"] for r in relations if r.get("type") == "image"), None)
    if image:
        data, credit = commons_photo(image)
        if data and (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
            PHOTO_PATH.parent.mkdir(parents=True, exist_ok=True)
            PHOTO_PATH.write_bytes(data)
            photo = {"file": PHOTO_PATH.name, **credit}
    profile = {
        "name": artist.get("name"), "born": (artist.get("life-span") or {}).get("begin"),
        "origin": (artist.get("begin-area") or {}).get("name"), "country": (artist.get("area") or {}).get("name"),
        "summary": summary, "summary_url": summary_url, "photo": photo,
        "links": grouped_links(relations),
        "musicbrainz": f"https://musicbrainz.org/artist/{LIL_WAYNE_MBID}",
    }
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Saved {PROFILE_PATH.relative_to(PROJECT_ROOT)}: {sum(len(v) for v in profile['links'].values())} links, "
          f"photo {'yes' if photo else 'no'}, summary {len(summary or '')} chars")


if __name__ == "__main__":
    main()

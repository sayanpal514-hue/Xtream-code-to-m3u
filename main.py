#!/usr/bin/env python3
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

AGENT = "IPTVSmarters/1.0"

BANNER = """
    ███████╗ █████╗ ██╗   ██╗ █████╗ ███╗   ██╗
    ██╔════╝██╔══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║
    ███████╗███████║ ╚████╔╝ ███████║██╔██╗ ██║
    ╚════██║██╔══██║  ╚██╔╝  ██╔══██║██║╚██╗██║
    ███████║██║  ██║   ██║   ██║  ██║██║ ╚████║
    ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝    S A Y A N
"""

def build_session():
    s = requests.Session()
    s.headers["User-Agent"] = AGENT
    retry = Retry(total=4, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504))
    ad = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("http://", ad)
    s.mount("https://", ad)
    return s

def call(sess, base, user, pwd, action=None, **extra):
    q = {"username": user, "password": pwd}
    if action:
        q["action"] = action
    q.update(extra)
    r = sess.get(base + "/player_api.php", params=q, timeout=(15, 180))
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return None

def clean(v):
    if v is None:
        return ""
    return str(v).replace("\r", " ").replace("\n", " ").strip()

def cats(sess, base, user, pwd, kind):
    data = call(sess, base, user, pwd, "get_" + kind + "_categories") or []
    out = {}
    for c in data:
        out[str(c.get("category_id"))] = clean(c.get("category_name")) or "Other"
    return out

def entry(fh, url, name, group="", tvg="", logo=""):
    name = clean(name) or "No name"
    fh.write('#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s" group-title="%s",%s\n'
             % (clean(tvg), name, clean(logo), clean(group), name))
    fh.write(url + "\n")

def live(sess, base, user, pwd, fh, ext):
    groups = cats(sess, base, user, pwd, "live")
    items = call(sess, base, user, pwd, "get_live_streams") or []
    for it in items:
        sid = it.get("stream_id")
        if sid is None:
            continue
        entry(fh, "%s/live/%s/%s/%s.%s" % (base, user, pwd, sid, ext),
              it.get("name"),
              group=groups.get(str(it.get("category_id")), "Live"),
              tvg=it.get("epg_channel_id"),
              logo=it.get("stream_icon"))
    return len(items)

def movies(sess, base, user, pwd, fh):
    groups = cats(sess, base, user, pwd, "vod")
    items = call(sess, base, user, pwd, "get_vod_streams") or []
    for it in items:
        sid = it.get("stream_id")
        if sid is None:
            continue
        ext = it.get("container_extension") or "mp4"
        entry(fh, "%s/movie/%s/%s/%s.%s" % (base, user, pwd, sid, ext),
              it.get("name"),
              group="Movies - " + groups.get(str(it.get("category_id")), "VOD"),
              logo=it.get("stream_icon"))
    return len(items)

def series(sess, base, user, pwd, fh, workers):
    groups = cats(sess, base, user, pwd, "series")
    shows = call(sess, base, user, pwd, "get_series") or []

    def grab(show):
        sid = show.get("series_id")
        if sid is None:
            return show, None
        try:
            return show, call(sess, base, user, pwd, "get_series_info", series_id=sid)
        except Exception:
            return show, None

    count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for show, info in pool.map(grab, shows):
            if not info:
                continue
            seasons = info.get("episodes")
            if not isinstance(seasons, dict):
                continue
            title = clean(show.get("name")) or "Series"
            logo = clean(show.get("cover"))
            group = "Series - " + groups.get(str(show.get("category_id")), "Series")
            for season, eps in seasons.items():
                if not isinstance(eps, list):
                    continue
                try:
                    sn = int(season)
                except (TypeError, ValueError):
                    sn = 0
                for ep in eps:
                    eid = ep.get("id")
                    if eid is None:
                        continue
                    ext = ep.get("container_extension") or "mp4"
                    try:
                        en = int(ep.get("episode_num") or 0)
                    except (TypeError, ValueError):
                        en = 0
                    tag = "S%02dE%02d" % (sn, en)
                    entry(fh, "%s/series/%s/%s/%s.%s" % (base, user, pwd, eid, ext),
                          "%s %s" % (title, tag), group=group, logo=logo)
                    count += 1
    return count

def normalize(server):
    server = server.strip()
    if not server.startswith("http://") and not server.startswith("https://"):
        server = "http://" + server
    return server.rstrip("/")

def prompt(value, text, secret=False):
    if value:
        return value
    return getpass(text) if secret else input(text).strip()

def main():
    print(BANNER)
    
    p = argparse.ArgumentParser(
        description="Extract an M3U playlist from an Xtream Codes server.",
        epilog="Usage examples:\n"
               "  python a.py -s server -u user -p pass\n"
               "  python a.py server user pass"
    )
    
    # Positional arguments (optional)
    p.add_argument("server_pos", nargs="?", help="Server URL (positional)")
    p.add_argument("username_pos", nargs="?", help="Username (positional)")
    p.add_argument("password_pos", nargs="?", help="Password (positional)")
    
    # Optional arguments (with flags)
    p.add_argument("-s", "--server", help="Server URL")
    p.add_argument("-u", "--username", help="Username")
    p.add_argument("-p", "--password", help="Password")
    p.add_argument("-o", "--output", default="playlist.m3u")
    p.add_argument("--live-ext", default="m3u8")
    p.add_argument("--no-live", action="store_true")
    p.add_argument("--no-vod", action="store_true")
    p.add_argument("--no-series", action="store_true")
    p.add_argument("-w", "--workers", type=int, default=8)
    
    a = p.parse_args()
    
    # Use positional if flags aren't provided
    server = a.server or a.server_pos
    username = a.username or a.username_pos
    password = a.password or a.password_pos

    base = normalize(prompt(server, "Server (host:port): "))
    user = prompt(username, "Username: ")
    pwd = prompt(password, "Password: ", secret=True)

    sess = build_session()
    info = call(sess, base, user, pwd) or {}
    if str(info.get("user_info", {}).get("auth")) != "1":
        print("Login failed. Check the server, username and password.", file=sys.stderr)
        return 1
    print("Connected as %s (%s)." % (user, info.get("user_info", {}).get("status", "?")),
          file=sys.stderr)

    nc = nm = ns = 0
    with open(a.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        if not a.no_live:
            print("Reading live channels...", file=sys.stderr)
            nc = live(sess, base, user, pwd, fh, a.live_ext)
        if not a.no_vod:
            print("Reading movies...", file=sys.stderr)
            nm = movies(sess, base, user, pwd, fh)
        if not a.no_series:
            print("Reading series (this one takes a while)...", file=sys.stderr)
            ns = series(sess, base, user, pwd, fh, a.workers)

    print("Saved %s" % a.output, file=sys.stderr)
    print("  channels: %d" % nc, file=sys.stderr)
    print("  movies:   %d" % nm, file=sys.stderr)
    print("  episodes: %d" % ns, file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())

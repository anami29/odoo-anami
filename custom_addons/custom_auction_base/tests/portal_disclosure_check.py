#!/usr/bin/env python3
"""Runtime disclosure check against a running instance.

The static suites cannot prove this one: whether the bidder portal leaks
anything has to be asked of real HTML served to a real portal session. Run
it against a staging instance with a published event and at least two
invited bidders.

    python3 tests/portal_disclosure_check.py \\
        --base http://localhost:8069 \\
        --event 10 \\
        --bidder bidder.a:BidPass!23 \\
        --other  bidder.b:BidPass!23 \\
        --secret 164000 --secret 2400

Every --secret is a value that must NOT appear in any page the portal
serves: the internal estimate, an undisclosed ceiling, a competitor's
price. Exit code 1 means something leaked.
"""
import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

DENIED_KEYS = [
    "estimated_value", "dek_wrapped", "dek_salt", "dek_fingerprint",
    "envelope_nonce", "payload_digest", "proxy_max", "norm_value",
]
FAILED = []


def check(name, ok, detail=""):
    print("  %-56s %s%s" % (name, "PASS" if ok else "FAIL",
                            "" if ok else "   " + detail))
    if not ok:
        FAILED.append(name)


class Session:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path):
        """Return (status, body). A 404 is an ANSWER here, not an error:
        probing an identifier that should not resolve is the point."""
        try:
            with self.opener.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def login(self, login, password):
        _, html = self.get("/web/login")
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        data = urllib.parse.urlencode({
            "login": login, "password": password,
            "csrf_token": m.group(1) if m else "", "redirect": "",
        }).encode()
        with self.opener.open(self.base + "/web/login", data) as r:
            return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8069")
    ap.add_argument("--event", type=int, required=True)
    ap.add_argument("--bidder", required=True, help="login:password")
    ap.add_argument("--other", help="login:password of a second bidder")
    ap.add_argument("--secret", action="append", default=[],
                    help="a value that must never appear. Repeatable.")
    args = ap.parse_args()

    login, password = args.bidder.split(":", 1)
    s = Session(args.base)
    s.login(login, password)

    pages = {}
    for path in ("/my/auctions",
                 "/my/auction/%d" % args.event,
                 "/my/auction/%d/bid" % args.event):
        status, body = s.get(path)
        pages[path] = body
        check("reachable %s" % path, status == 200, "HTTP %s" % status)
    blob = "".join(pages.values())

    print("\nDenylist keys")
    for key in DENIED_KEYS:
        check("absent: %s" % key, key not in blob)

    print("\nSupplied secrets")
    for secret in args.secret:
        plain = str(secret)
        with_commas = "{:,}".format(int(float(plain))) if plain.replace(
            ".", "").isdigit() else plain
        leaked = plain in blob or with_commas in blob
        check("absent: %s" % plain, not leaked)

    print("\nIdentifier probing")
    for bogus in (args.event + 9999, 999999):
        status, _ = s.get("/my/auction/%d" % bogus)
        check("event %d is 404, not someone else's tender" % bogus,
              status in (404, 403), "HTTP %s" % status)

    if args.other:
        print("\nCross-bidder")
        olog, opwd = args.other.split(":", 1)
        o = Session(args.base)
        o.login(olog, opwd)
        _, obody = o.get("/my/auction/%d" % args.event)
        # the first bidder's own bid reference must not appear for the other
        refs = set(re.findall(r"BID/\d{4}/\d+", blob))
        orefs = set(re.findall(r"BID/\d{4}/\d+", obody))
        check("no bid reference crosses between bidders",
              not (refs & orefs), "shared: %s" % (refs & orefs))
        utrs = set(re.findall(r"\bN\d{10,}\b", blob))
        check("no instrument reference crosses between bidders",
              not any(u in obody for u in utrs))

    print("\n%d check(s) failed\n" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

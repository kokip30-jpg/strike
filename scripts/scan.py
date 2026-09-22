#!/usr/bin/env python3
"""Strike – hledá strike pro krytý call. Data: Alpaca (akcie IEX, opce indicative), Nasdaq (výsledky)."""
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
CFG = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
AK, AS = os.environ.get('ALPACA_KEY', '').strip(), os.environ.get('ALPACA_SECRET', '').strip()
NY = ZoneInfo('America/New_York')
NOW = datetime.now(timezone.utc)
ERRORS = []


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def http(url, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if len(ERRORS) < 20:
                ERRORS.append({'url': url[:140], 'code': e.code})
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(3 * (i + 1))
                continue
            return None
        except Exception as e:
            if len(ERRORS) < 20:
                ERRORS.append({'url': url[:140], 'err': repr(e)[:100]})
            time.sleep(2 * (i + 1))
    return None


def alpaca(path, params):
    b = http('https://data.alpaca.markets' + path + '?' + urllib.parse.urlencode(params),
             {'APCA-API-KEY-ID': AK, 'APCA-API-SECRET-KEY': AS, 'Accept': 'application/json'})
    return json.loads(b) if b else None


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, name)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(p + '.tmp', p)


# ------------------------------------------------------------ Black–Scholes
def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(S, K, T, r, v):
    d1 = (math.log(S / K) + (r + v * v / 2) * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2), ncdf(d1)


def implied_vol(price, S, K, T, r):
    if price <= max(S - K * math.exp(-r * T), 0) + 1e-6:
        return None
    lo, hi = 0.01, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_call(S, K, T, r, mid)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def occ(sym):
    m = re.match(r'^[A-Z.]+(\d{2})(\d{2})(\d{2})([CP])(\d{8})$', sym)
    return (f'20{m.group(1)}-{m.group(2)}-{m.group(3)}', m.group(4), int(m.group(5)) / 1000) if m else None


# ------------------------------------------------------------ ceny akcií
def stock_prices(symbols):
    out = {}
    for i in range(0, len(symbols), 50):
        j = alpaca('/v2/stocks/snapshots', {'symbols': ','.join(symbols[i:i + 50]), 'feed': 'iex'})
        for sym, sn in (j or {}).items():
            if not isinstance(sn, dict):
                continue
            lt = sn.get('latestTrade') or {}
            db, pdb = sn.get('dailyBar') or {}, sn.get('prevDailyBar') or {}
            price = lt.get('p') or db.get('c') or pdb.get('c')
            if price:
                out[sym] = {'p': price, 'pc': pdb.get('c'), 't': lt.get('t')}
    return out


# ------------------------------------------------------------ výsledky hospodaření (Nasdaq)
def earnings(meta):
    today = NOW.astimezone(NY).date()
    cache = load('earnings.json', {})
    if meta.get('earn_at') == today.isoformat() and cache:
        return cache
    hdr = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
           'Accept': 'application/json, text/plain, */*', 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}
    want = set(CFG['tickers'])
    out, ok = {}, 0
    for n in range(0, CFG.get('dte_max', 70) + 1):
        d = today + timedelta(days=n)
        if d.weekday() > 4:
            continue
        b = http(f'https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}', hdr)
        if not b:
            continue
        ok += 1
        try:
            rows = ((json.loads(b).get('data') or {}).get('rows')) or []
        except ValueError:
            continue
        for r in rows:
            s = (r.get('symbol') or '').upper()
            if s in want and s not in out:
                t = (r.get('time') or '').lower()
                out[s] = {'d': d.isoformat(), 'h': 'pre' if 'pre' in t else 'post' if 'after' in t else None}
        time.sleep(0.4)
    if ok:
        save('earnings.json', out)
        meta['earn_at'] = today.isoformat()
        log('výsledky: nalezeno', len(out), 'termínů')
        return out
    return cache


# ------------------------------------------------------------ opce
def scan(sym, S, today):
    r = CFG.get('rate', 0.04)
    snaps, token = {}, None
    for _ in range(8):
        q = {'feed': 'indicative', 'type': 'call', 'limit': 1000,
             'expiration_date_gte': (today + timedelta(days=CFG['dte_min'])).isoformat(),
             'expiration_date_lte': (today + timedelta(days=CFG['dte_max'])).isoformat(),
             'strike_price_gte': round(S * CFG['strike_min'], 2), 'strike_price_lte': round(S * CFG['strike_max'], 2)}
        if token:
            q['page_token'] = token
        j = alpaca(f'/v1beta1/options/snapshots/{sym}', q)
        if not j:
            break
        snaps.update(j.get('snapshots') or {})
        token = j.get('next_page_token')
        if not token:
            break
    by_exp = {}
    for osym, sn in snaps.items():
        p = occ(osym)
        if not p or p[1] != 'C':
            continue
        exp, _, K = p
        qt = sn.get('latestQuote') or {}
        bid, ask = qt.get('bp') or 0, qt.get('ap') or 0
        if bid <= 0 or ask < bid:
            continue
        by_exp.setdefault(exp, []).append((K, bid, ask, (sn.get('greeks') or {}).get('delta'), sn.get('impliedVolatility')))
    exps = sorted(by_exp)
    chosen = []
    for target in CFG['expiry_targets']:
        if exps:
            e = min(exps, key=lambda x: abs((datetime.fromisoformat(x).date() - today).days - target))
            if e not in chosen:
                chosen.append(e)
    out = []
    for exp in sorted(chosen):
        dte = max((datetime.fromisoformat(exp).date() - today).days, 1)
        T = dte / 365
        chain = []
        for K, bid, ask, delta, iv in sorted(by_exp[exp]):
            mid = (bid + ask) / 2
            iv = iv or implied_vol(mid, S, K, T, r)
            if delta is None and iv:
                delta = bs_call(S, K, T, r, iv)[1]
            if delta is None:
                continue
            chain.append({'k': K, 'b': bid, 'a': ask, 'd': round(delta, 3),
                          'iv': round(iv * 100, 1) if iv else None,
                          'sp': round((ask - bid) / mid * 100, 1) if mid else None})
        if chain:
            out.append({'e': exp, 'n': dte, 'c': chain})
    return out


def main():
    if not (AK and AS):
        log('chybí klíče Alpaca (ALPACA_KEY, ALPACA_SECRET)')
        save('options.json', {'updated': NOW.isoformat(), 'error': 'Chybí klíče Alpaca', 'rows': []})
        return
    meta = load('meta.json', {})
    syms = [s.upper() for s in CFG['tickers']]
    today = NOW.astimezone(NY).date()
    prices = stock_prices(syms)
    log('ceny akcií:', len(prices))
    try:
        earn = earnings(meta)
    except Exception as e:
        log('výsledky selhaly', repr(e))
        earn = load('earnings.json', {})
    rows = []
    for sym in syms:
        pr = prices.get(sym)
        if not pr:
            continue
        try:
            exps = scan(sym, pr['p'], today)
        except Exception as e:
            log('CHYBA', sym, repr(e))
            continue
        if exps:
            rows.append({'t': sym, 's': round(pr['p'], 2), 'pc': pr.get('pc'), 'er': earn.get(sym), 'x': exps})
    save('options.json', {'updated': NOW.isoformat(), 'rows': rows})
    meta['updated'] = NOW.isoformat()
    save('meta.json', meta)
    save('debug.json', {'errors': ERRORS})
    log('hotovo:', len(rows), 'titulů')


if __name__ == '__main__':
    main()

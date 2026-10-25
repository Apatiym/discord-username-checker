# -*- coding: utf-8 -*-
"""Discord short username checker and nick changer.

  taken    -> red
  free     -> green

The token is read from the Discord folder (DPAPI + AES-GCM).
Run: start.bat  or  python ds_username.py
"""

import getpass
import json
import os
import queue
import random
import string
import sys
import threading
import time

import creds
import net
import ui

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
API = "/api/v9"

# live sing-box pool; None when the VPN layer is off
_POOL = None

TAKEN, FREE, INVALID, LIMITED, AUTH, ERROR = (
    "taken", "free", "invalid", "limited", "auth", "error",
)

# ---------------- colors (no emoji) ----------------

def enable_colors():
    if os.name == "nt":
        os.system("")  # enables ANSI sequences in cmd
    # logo blocks and any non-ASCII name: without this cp1251 breaks print
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

def C(code, text):
    return f"\033[{code}m{text}\033[0m"

def red(t):    return C("31", t)
def green(t):  return C("92", t)
def free(t):   return C("1;92", t)
def yellow(t): return C("33", t)
def gray(t):   return ui.muted(t)

def title(text):
    """Section title: gradient text typed out character by character."""
    print()
    ui.typed(text)
    print()

def ask(text):
    """Input prompt in the theme colors."""
    if text.startswith("> "):
        return input("  " + ui.accent(">") + ui.muted(" " + text[2:]))
    return input("  " + ui.muted(text))

def out(text):
    """Prints a permanent line above the live scan line."""
    ui.write_line(text)

def pause():
    try:
        input(ui.muted("\n  Enter"))
    except EOFError:
        pass

def label(text, width=8):
    return ui.muted(text.ljust(width))

# ---------------- config ----------------

DEFAULTS = {
    "mode": "safe",
    "delay": 1.0,
    "workers": 1,
    "use_ip": False,
    "vpn": {"enabled": False, "size": 12},
}


def load_config():
    """Config with the defaults filled in, so a fresh clone needs no file."""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = data
        except Exception:
            cfg = {}
    for key, value in DEFAULTS.items():
        cfg.setdefault(key, dict(value) if isinstance(value, dict) else value)
    return cfg

_config_lock = threading.Lock()

def save_config(cfg):
    """Writes config atomically: parallel workers cannot truncate the file."""
    with _config_lock:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)

# ---------------- Discord API ----------------

def api_me(client, token):
    """Checks a token: (True, me) ok, (False, data) rejected, (None, {}) no network.

    The three states matter: a dead network must not be mistaken for a dead
    token, otherwise a VPN hiccup wipes a perfectly good token from config.
    """
    try:
        code, data = client.request(
            "GET", f"{API}/users/@me", headers={"Authorization": token}
        )
    except Exception:
        return None, {}
    if code == 200 and data.get("id"):
        return True, data
    return False, data

def check_username(client, name, token=None, channel="auto"):
    """Checks whether a name is taken. Returns (status, retry_after).

    channel="token" - only pomelo-attempt (per-account limit, ~150 checks);
    channel="ip"    - only username-attempt-unauthed (per-IP limit, ~19 checks);
    channel="auto"  - with a token try pomelo first.
    """
    if token and channel in ("auto", "token"):
        code, data = client.request(
            "POST", f"{API}/users/@me/pomelo-attempt",
            {"username": name}, {"Authorization": token},
        )
        if code == 200 and "taken" in data:
            return (TAKEN if data["taken"] else FREE), 0
        if code == 400:
            return INVALID, 0
        if code == 401:
            return AUTH, 0
        if code == 429:
            return LIMITED, float(data.get("retry_after", 1))
        if channel == "token":
            return ERROR, 0

    code, data = client.request(
        "POST", f"{API}/unique-username/username-attempt-unauthed",
        {"username": name},
    )
    if code == 200 and "taken" in data:
        return (TAKEN if data["taken"] else FREE), 0
    if code == 429:
        return LIMITED, float(data.get("retry_after", 1))
    if code in (400, 404):
        return INVALID, 0
    return ERROR, 0

def set_username(client, token, new_name, password=None):
    """PATCH /users/@me. Returns (ok, error_code_or_text)."""
    payload = {"username": new_name}
    if password:
        payload["password"] = password
    code, data = client.request(
        "PATCH", f"{API}/users/@me", payload, {"Authorization": token}
    )
    if code == 200:
        return True, ""

    errors = data.get("errors") or {}
    if data.get("captcha_key") or data.get("captcha_sitekey"):
        return False, "captcha"
    if errors.get("password"):
        return False, "password"
    if code == 429:
        return False, "rate limited, try later"
    msg = data.get("message", f"HTTP {code}")
    if errors:
        msg += " " + json.dumps(errors, ensure_ascii=False)
    return False, msg

# ---------------- token ----------------

def ensure_token(cfg, verbose=True, mark=None):
    """Finds a working token. Returns it, or None when there is nothing to use."""
    def stage(i):
        if mark:
            mark(i)

    token = cfg.get("token")
    if token:
        stage(3)
        client = net.Client()
        try:
            ok, data = api_me(client, token)
        finally:
            client.close()
        if ok:
            cfg["me"] = data.get("username", "")
            save_config(cfg)
            return token
        if ok is None:
            # no network: the token may be fine, so keep it and let the caller
            # report the connection problem instead of wiping the account
            if verbose:
                print(yellow("  can't reach Discord, keeping the stored token"))
            return token
        if verbose:
            print(gray("  stored token is dead, looking for a new one"))
        cfg.pop("token", None)
        cfg.pop("me", None)
        save_config(cfg)

    stage(2)
    network_down = False
    try:
        found = creds.collect(
            cfg.get("discord_path"), log=print if verbose else (lambda *a: None)
        )
    except Exception as exc:
        if verbose:
            print(yellow(f"  token search failed: {exc}"))
        found = []

    for cand, _dir, _how in found:
        stage(3)
        client = net.Client()
        try:
            ok, data = api_me(client, cand)
        finally:
            client.close()
        if ok:
            cfg["token"] = cand
            cfg["me"] = data.get("username", "")
            save_config(cfg)
            if verbose:
                print(green(f"  token accepted: {cfg['me']}"))
            return cand
        if ok is None:
            network_down = True
    if verbose:
        if network_down:
            print(yellow("  no connection, check your proxy or VPN"))
        else:
            print(yellow("  no token found, start Discord first"))
    return None

def change_nick(cfg, name):
    token = ensure_token(cfg, verbose=False)
    if not token:
        print(red("  no token"))
        return

    client = net.Client()
    spin = ui.Loader(("renaming",), width=16, min_time=0.45)
    try:
        ok, err = spin.run(lambda _mark: set_username(client, token, name))
        if not ok and err == "password":
            print(gray("  Discord asks for the account password"))
            try:
                pw = getpass.getpass(ui.muted("  password: "))
            except Exception:
                pw = input(ui.muted("  password: "))
            ok, err = spin.run(lambda _mark: set_username(client, token, name, pw))
        if ok:
            ui.reveal(name, green("  name changed"))
        elif err == "captcha":
            print(red("  Discord wants a captcha"))
            print(gray("  change the nick manually in the client"))
        else:
            print(red(f"  failed: {err}"))
    except Exception as exc:
        print(red(f"  failed: {exc}"))
    finally:
        client.close()

# ---------------- VPN pool ----------------

def vpn_settings(cfg):
    """Normalized VPN settings: {'enabled': bool, 'size': int}."""
    raw = cfg.get("vpn")
    if not isinstance(raw, dict):
        raw = {}
    try:
        size = int(raw.get("size", 12))
    except (TypeError, ValueError):
        size = 12
    return {"enabled": bool(raw.get("enabled", False)),
            "size": max(1, min(40, size))}


def ip_count(n):
    """'1 IP' / '3 IPs' - keeps the status line readable."""
    return f"{n} IP" + ("" if n == 1 else "s")


def stop_pool():
    """Kills sing-box and forgets the pool."""
    global _POOL
    if _POOL is not None:
        try:
            _POOL.stop()
        except Exception:
            pass
        _POOL = None
    net.use_pool(None)


def start_pool(cfg, mark=None):
    """Builds the VPN pool. Returns (pool or None, note for the user)."""
    global _POOL
    stop_pool()
    vcfg = vpn_settings(cfg)
    if not vcfg["enabled"]:
        return None, ""

    if mark:
        mark(4)
    if not net.find_singbox():
        return None, yellow("  sing-box not found, install Happ")

    nodes = net.load_nodes()
    if not nodes:
        return None, yellow("  no VPN nodes, check the connection")

    pool = net.Pool(nodes, size=vcfg["size"])
    try:
        alive = pool.start()
    except Exception as exc:
        return None, yellow(f"  VPN pool failed: {exc}")
    if not alive:
        return None, yellow("  VPN pool is empty, using the usual route")

    _POOL = pool
    net.use_pool(pool)
    return pool, green(f"  VPN pool: {ip_count(alive)} rotating")


# ---------------- scanning ----------------

class Gate:
    """Shared pause for all workers on a rate limit."""

    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0.0
        self.last_announce = 0.0

    def wait(self, stop):
        while not stop.is_set():
            with self.lock:
                left = self.until - time.time()
            if left <= 0:
                return
            stop.wait(min(left, 0.5))

    def pause(self, seconds):
        """Sets a pause. True - if it is worth announcing once."""
        now = time.time()
        with self.lock:
            new_until = now + max(0.0, seconds)
            announce = seconds > 5 and now - self.last_announce > 5
            self.until = max(self.until, new_until)
            if announce:
                self.last_announce = now
            return announce


class Pacer:
    """AIMD speed regulator: creeps up, brakes hard on 429.

    The point is to ride the edge of the limit instead of hitting the lock.
    A lock costs 5+ minutes and compounds (measured: 5 min -> 32 min), so
    giving up some speed is cheaper than getting flagged.
    """

    MIN = 0.0
    MAX = 4.0
    STEP = 0.012      # additive speed-up per success
    SLOWDOWN = 2.2    # multiplicative brake on a soft 429

    def __init__(self, delay=1.0):
        self.lock = threading.Lock()
        self.delay = max(0.0, min(self.MAX, float(delay)))
        # lower bound: safe mode never accelerates to zero, otherwise it turns
        # into a burst and a lock. Fast mode starts at 0, so it has no floor.
        self.floor = min(0.4, self.delay)
        self.ok_count = 0
        self.hit_count = 0

    def ok(self):
        with self.lock:
            self.ok_count += 1
            self.delay = max(self.floor, self.delay - self.STEP)

    def hit(self, retry):
        """Soft limit (small retry_after) - slow down and wait it out."""
        with self.lock:
            self.hit_count += 1
            if retry <= 3:
                self.delay = min(self.MAX, max(self.delay * self.SLOWDOWN, retry + 0.2))

    def sleep(self, stop):
        with self.lock:
            d = self.delay
        if d > 0:
            stop.wait(d)

    def label(self):
        with self.lock:
            rate = 0.0 if self.delay <= 0 else 1.0 / self.delay
            return f"{self.delay:.2f}s (~{rate:.1f}/s)"


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.checked = 0
        self.taken = 0
        self.free = []
        self.invalid = 0
        self.errors = 0
        self.limits = 0
        self.locked_until = 0.0
        self.net_down = False
        self.final_delay = None
        self.started = time.time()

    def add(self, status, name=None):
        with self.lock:
            if status == FREE:
                if name:
                    self.free.append(name)
            elif status == TAKEN:
                self.taken += 1
            elif status == INVALID:
                self.invalid += 1
            elif status == ERROR:
                self.errors += 1
            elif status == LIMITED:
                self.limits += 1

    def note_lock(self, seconds):
        with self.lock:
            self.locked_until = max(self.locked_until, time.time() + seconds)

    def rate(self):
        elapsed = max(0.001, time.time() - self.started)
        return self.checked / elapsed


# above this retry_after it is a real lock, waiting for it makes no sense
HARD_LIMIT = 25.0
# Margin on top of a lock. Important: a request during a lock EXTENDS it
# (measured: 5 min turned into 32 min), so wait long and do not poke.
LOCK_MARGIN = 180.0

MODES = {
    "safe": {"name": "safe", "delay": 1.0},
    "fast": {"name": "fast", "delay": 0.0},
    "custom": {"name": "custom", "delay": 1.0},
}


def fmt_left(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


CHANNELS = ("token", "ip")
CHANNEL_NAMES = {"token": "account", "ip": "IP"}


def locks(cfg):
    """Map channel -> timestamp until which it is blocked.

    Read-only on purpose: this is called in the inner scan loop, and writing
    config.json there would mean a disk write per name plus a race between
    worker threads.
    """
    data = cfg.get("locks")
    if not isinstance(data, dict):
        return {}
    now = time.time()
    fresh = {}
    for key, value in data.items():
        if key not in CHANNELS:
            continue
        try:
            until = float(value)
        except (TypeError, ValueError):
            continue
        if until > now:
            fresh[key] = until
    if fresh != data:
        cfg["locks"] = fresh
    return fresh


def lock_left(cfg, channel=None):
    """Seconds left before checking is allowed again."""
    data = locks(cfg)
    if channel:
        return max(0.0, data.get(channel, 0) - time.time())
    if not data:
        return 0.0
    return max(0.0, max(data.values()) - time.time())


def set_lock(cfg, seconds, channel="ip"):
    """Remembers a lock with a margin - Discord is strict right after one."""
    data = locks(cfg)
    data[channel] = time.time() + seconds + LOCK_MARGIN
    cfg["locks"] = data
    save_config(cfg)


def pool_alive():
    """True when the VPN pool has at least one working node."""
    return _POOL is not None and bool(_POOL.proxies)


def pick_channel(cfg, token, allow_ip=False):
    """Which channel is free right now. None - nothing to check with.

    The IP channel is off by default: it gives ~19 checks and then a
    25-35 minute lock, which is a worse deal than waiting for the account one.
    With a live VPN pool that changes: a lock belongs to one node, so the next
    node brings a fresh IP and the same channel keeps going.
    """
    if token and lock_left(cfg, "token") <= 0:
        return "token"
    # without a token there is no choice - only the IP channel is left
    if (allow_ip or not token) and (pool_alive() or lock_left(cfg, "ip") <= 0):
        return "ip"
    return None


def scan(cfg, token, names, workers, delay=0.0, allow_ip=False):
    """Checks names in several threads. Returns (first free name or None, stats)."""
    q = queue.Queue()
    for n in names:
        q.put(n)

    stop = threading.Event()
    gate = Gate()
    pacer = Pacer(delay)
    stats = Stats()
    client = net.Client()  # every thread gets its own keep-alive session
    state = {"token": token, "net_fail": 0}
    found = {"name": None}

    line = ui.ScanLine(len(names))
    line.start()

    def refresh():
        line.update(
            done=stats.checked, taken=stats.taken, free=len(stats.free),
            invalid=stats.invalid, errors=stats.errors, limits=stats.limits,
            rate=stats.rate(), delay=pacer.delay,
        )

    def worker():
        while not stop.is_set():
            try:
                name = q.get_nowait()
            except queue.Empty:
                return

            while not stop.is_set():
                gate.wait(stop)
                if stop.is_set():
                    return

                channel = pick_channel(cfg, state["token"], allow_ip)
                if channel is None:
                    # both channels locked - waiting inside the console is pointless
                    left = max(lock_left(cfg, "token") if state["token"] else 0,
                               lock_left(cfg, "ip"))
                    stats.note_lock(left)
                    stop.set()
                    return

                try:
                    status, retry = check_username(
                        client, name, state["token"], channel=channel
                    )
                except Exception:
                    status, retry = ERROR, 0

                if status == LIMITED:
                    stats.add(LIMITED)
                    pacer.hit(retry)
                    if retry > HARD_LIMIT:
                        if channel == "ip" and pool_alive():
                            # the lock belongs to this node's IP, not to the
                            # whole channel: drop the node, the next one is free
                            client.fail_current()
                            out(yellow("  IP locked, switching node"))
                            continue
                        # this channel burned out - move to the other one
                        set_lock(cfg, retry, channel)
                        out(yellow(f"  {CHANNEL_NAMES[channel]} locked for"
                                   f" {fmt_left(retry)}, switching channel"))
                        continue
                    if gate.pause(retry + 0.15):
                        out(gray(f"  small limit {retry:.0f}s"))
                    continue
                if status == AUTH:
                    state["token"] = None
                    out(yellow("  token rejected, falling back to IP"))
                    continue
                break

            if stop.is_set():
                return

            with stats.lock:
                stats.checked += 1

            if status == TAKEN:
                out(f"  {label('taken')} {red(name)}")
                pacer.ok()
                state["net_fail"] = 0
            elif status == FREE:
                out(f"  {label('free')} {free(name)}")
                stats.add(FREE, name)
                found["name"] = name
                stop.set()
                return
            elif status == INVALID:
                out(f"  {label('invalid')} {gray(name)}")
                pacer.ok()
                state["net_fail"] = 0
            elif status == ERROR:
                out(f"  {label('error')} {yellow(name)}")
                state["net_fail"] += 1
                if state["net_fail"] >= 3:
                    # three dead requests in a row: the route is down, not the name
                    out(yellow("  connection lost, stopping"))
                    stats.net_down = True
                    stop.set()
                    return
            stats.add(status)
            refresh()

            pacer.sleep(stop)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.3)
    except KeyboardInterrupt:
        stop.set()
        out(gray("\n  stopped"))
    finally:
        line.stop()
        client.close()

    stats.final_delay = pacer.delay
    return found["name"], stats

# ---------------- interface ----------------

def ask_int(prompt, default, lo, hi):
    raw = input(ui.muted("  " + prompt)).strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if lo <= v <= hi else default
    except ValueError:
        return default

def ask_float(prompt, default):
    raw = input(ui.muted("  " + prompt)).strip().replace(",", ".")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def _interactive_input():
    """True on a Windows console with a real keyboard (raw key reads)."""
    if os.name != "nt":
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _read_key():
    """Reads one keypress without Enter: arrows, W/S, Enter, Q/Esc.

    On Windows, arrow keys come back as an extended code (\x00 or \xe0)
    followed by the actual key (H = up, P = down).
    """
    import msvcrt
    while True:
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
        ch = msvcrt.getwch()
        if ch == "\x03":  # Ctrl+C: raw mode swallows it otherwise
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):  # extended key (arrows, F-keys)
            k = msvcrt.getwch()
            if k == "H":
                return "up"
            if k == "P":
                return "down"
            continue
        c = ch.lower()
        if c == "w":
            return "up"
        if c == "s":
            return "down"
        if c in ("\r", "\n"):
            return "enter"
        if c in ("q", "0", "\x1b"):
            return "quit"
        if c.isdigit():
            index = int(c) - 1
            if 0 <= index < len(MENU_ITEMS):
                return MENU_ITEMS[index][1]  # number shortcut straight to an action
        return None

def settings_menu(cfg):
    clear()
    title("settings")

    current = cfg.get("discord_path")
    print(f"  {label('path', 9)} {gray(current or 'not set')}"
          f"  {green('ok') if has_leveldb(normalize_path(current)) else yellow('no data')}")
    print(f"    {ui.accent('1')} auto-detect    {ui.accent('2')} enter manually"
          f"    {ui.accent('3')} keep")
    p = ask("> ").strip()
    if p == "1":
        cfg.pop("discord_path", None)
        if autodetect(cfg):
            print(green(f"  found: {cfg['discord_path']}"))
        else:
            print(yellow("  nothing found, enter it by hand"))
    elif p == "2":
        raw = ask("folder: ").strip()
        good = normalize_path(raw)
        if good:
            cfg["discord_path"] = good
            save_config(cfg)
            print(green("  saved"))
        elif raw and os.path.isdir(expand_path(raw)):
            cfg["discord_path"] = expand_path(raw)
            save_config(cfg)
            print(yellow('  saved, but no "Local Storage\\leveldb" yet - start Discord once'))
        elif raw:
            print(red("  no such folder"))

    print()
    print(f"  {label('token', 9)} {green('set') if cfg.get('token') else red('none')}"
          + (gray(f"  {cfg['me']}") if cfg.get("me") else ""))
    print(f"    {ui.accent('1')} find    {ui.accent('2')} paste    {ui.accent('3')} forget")
    print(gray("    stored in config.json next to the program, never in the repo"))
    t = ask("> ").strip()
    if t == "1":
        cfg.pop("token", None)
        ensure_token(cfg)
    elif t == "2":
        raw = ask("token: ").strip()
        if raw:
            client = net.Client()
            try:
                ok, data = api_me(client, raw)
            finally:
                client.close()
            if ok:
                cfg["token"] = raw
                cfg["me"] = data.get("username", "")
                save_config(cfg)
                print(green(f"  accepted: {cfg['me']}"))
            elif ok is None:
                print(yellow("  no connection, check your proxy or VPN"))
            else:
                print(red("  rejected"))
    elif t == "3":
        cfg.pop("token", None)
        cfg.pop("me", None)
        save_config(cfg)
        print(gray("  forgotten"))

    print()
    print(f"  {label('workers', 9)} {gray(cfg.get('workers', 1))}")
    w = ask_int("new value, Enter to keep: ", 0, 1, 8)
    if w:
        cfg["workers"] = w

    mode = cfg.get("mode", "safe")
    print()
    print(f"  {label('mode', 9)} {ui.accent(MODES.get(mode, MODES['safe'])['name'])}"
          f"  {gray(str(cfg.get('delay', 1.0)) + 's')}")
    print(f"    {ui.accent('1')} safe    {ui.accent('2')} fast    {ui.accent('3')} custom delay")
    m = ask("> ").strip()
    if m == "1":
        cfg["mode"] = "safe"
        cfg["delay"] = MODES["safe"]["delay"]
    elif m == "2":
        cfg["mode"] = "fast"
        cfg["delay"] = 0.0
    elif m == "3":
        d = ask_float("delay, seconds: ", -1)
        if d >= 0:
            cfg["mode"] = "custom"
            cfg["delay"] = d
    save_config(cfg)

    use_ip = bool(cfg.get("use_ip", False))
    print()
    print(f"  {label('IP channel', 9)} {green('on') if use_ip else gray('off')}")
    if vpn_settings(cfg)["enabled"]:
        print(gray("    with the VPN pool a lock costs one node, not the channel"))
    else:
        print(gray("    ~19 checks, then a 25-35 min lock"))
    if ask("toggle? (y/N): ").strip().lower() == "y":
        cfg["use_ip"] = not use_ip
    save_config(cfg)

    settings_vpn(cfg)

    print()
    print(f"  {label('lock', 9)} account {gray(fmt_left(lock_left(cfg, 'token')))}"
          f"   IP {gray(fmt_left(lock_left(cfg, 'ip')))}")
    if ask("reset? (y/N): ").strip().lower() == "y":
        cfg.pop("locks", None)
        print(gray("  reset"))
    save_config(cfg)
    pause()


def settings_vpn(cfg):
    """VPN block inside settings: toggle, pool size, refresh the node list."""
    vcfg = vpn_settings(cfg)
    alive, dead = net.pool_status()

    print()
    if vcfg["enabled"]:
        state = green(f"on  {ip_count(alive)}") if alive else yellow("on  no live nodes")
    else:
        state = gray("off")
    print(f"  {label('vpn', 9)} {state}")
    print(gray("    free nodes from github.com/igareck/vpn-configs-for-russia"))
    print(f"    {ui.accent('1')} toggle    {ui.accent('2')} pool size"
          f"    {ui.accent('3')} refresh list")
    choice = ask("> ").strip()

    if choice == "1":
        cfg["vpn"] = {"enabled": not vcfg["enabled"], "size": vcfg["size"]}
        save_config(cfg)
        if cfg["vpn"]["enabled"]:
            print(gray("  starting the pool, this takes a few seconds"))
            pool, msg = start_pool(cfg)
            print(msg or gray("  pool is off"))
        else:
            stop_pool()
            print(gray("  pool stopped"))
    elif choice == "2":
        size = ask_int(f"pool size 1-40 [{vcfg['size']}]: ", vcfg["size"], 1, 40)
        cfg["vpn"] = {"enabled": vcfg["enabled"], "size": size}
        save_config(cfg)
        print(gray(f"  size {size}, applies on the next start"))
    elif choice == "3":
        try:
            os.remove(net.CACHE)
        except OSError:
            pass
        nodes = net.load_nodes(refresh=True)
        print(gray(f"  {len(nodes)} nodes parsed"))
        if vcfg["enabled"]:
            pool, msg = start_pool(cfg)
            print(msg or gray("  pool is off"))
    save_config(cfg)

def has_leveldb(path):
    """True when this folder really holds client data, not just the app."""
    return bool(path) and os.path.isdir(os.path.join(path, "Local Storage", "leveldb"))


def expand_path(raw):
    """Strips quotes, expands %VARS% and ~, normalizes separators."""
    if not raw:
        return ""
    text = raw.strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(text)))


def normalize_path(raw):
    """Whatever the user typed -> the folder that holds client data, or None.

    People paste all kinds of things: the install folder, Local State, the
    leveldb folder itself, a path in quotes, %APPDATA%\\discord. All of them
    should end up at the same folder.
    """
    text = expand_path(raw)
    if not text:
        return None
    if os.path.isfile(text) and os.path.basename(text).lower() == "local state":
        text = os.path.dirname(text)
    tail = os.path.join("Local Storage", "leveldb")
    if text.lower().endswith(tail.lower()):
        text = text[: -len(tail)].rstrip("\\/")
    if has_leveldb(text):
        return text

    # an install folder (AppData\Local\Discord) has no data; the client keeps
    # it next door in Roaming under the same name
    name = os.path.basename(text.rstrip("\\/")).lower()
    roaming = os.environ.get("APPDATA") or ""
    if name and os.path.isdir(roaming):
        try:
            entries = os.listdir(roaming)
        except OSError:
            entries = []
        for entry in entries:
            # take the name as it is on disk: "Discord" and "discord" are the
            # same folder to Windows but not to a string comparison
            if entry.lower() == name:
                cand = os.path.join(roaming, entry)
                if has_leveldb(cand):
                    return cand
    return None


def find_discord():
    """First folder that actually holds client data: stable, PTB, Canary."""
    home = os.path.expanduser("~")
    roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")

    for name in ("discord", "discordptb", "discordcanary", "discorddevelopment"):
        cand = os.path.join(roaming, name)
        if has_leveldb(cand):
            return cand

    # anything else that looks like a Discord build
    if os.path.isdir(roaming):
        try:
            for name in sorted(os.listdir(roaming)):
                cand = os.path.join(roaming, name)
                if name.lower().startswith("discord") and has_leveldb(cand):
                    return cand
        except OSError:
            pass
    return None


def autodetect(cfg):
    """Finds a Discord data folder. True when cfg holds one that has data.

    A stored path wins only if it really has Local Storage\\leveldb - otherwise
    a folder typed once by mistake would stick forever and block detection.
    """
    current = cfg.get("discord_path")
    good = normalize_path(current)
    if good:
        if good != current:
            cfg["discord_path"] = good
            save_config(cfg)
        return True

    found = find_discord()
    if found:
        cfg["discord_path"] = found
        save_config(cfg)
        return True
    return False


def ask_discord_path(cfg):
    """Asks for the client folder when detection found nothing.

    Runs after the boot animation on purpose: input() inside the live line
    would fight with the spinner.
    """
    print()
    print(yellow("  Discord folder not found"))
    print(gray('  it is the folder that holds "Local State"'))
    print(gray("  usually %APPDATA%\\discord"))
    if not _interactive_input():
        print(gray("  set it later in settings -> path"))
        return False

    for _ in range(3):
        raw = ask("folder, Enter to skip: ").strip()
        if not raw:
            break
        good = normalize_path(raw)
        if good:
            cfg["discord_path"] = good
            save_config(cfg)
            print(green(f"  saved: {good}"))
            return True
        print(yellow('  no "Local Storage\\leveldb" there - start Discord once'))
    print(gray("  skipped, set it later in settings -> path"))
    return False

def offer_set(cfg, name):
    if input(ui.muted("\n  set it as your nick? (y/N): ")).strip().lower() != "y":
        return
    change_nick(cfg, name)

def gen_unique(count, length, alphabet):
    """Random names of one length; never asks for more than the space holds.

    Without the cap, asking for 5000 three-digit names (only 1000 exist)
    would spin forever in the while loop.
    """
    space = len(alphabet) ** length
    count = min(count, space)
    seen, out_names = set(), []
    while len(out_names) < count:
        n = "".join(random.choices(alphabet, k=length))
        if n not in seen:
            seen.add(n)
            out_names.append(n)
    return out_names

def guard_lock(cfg, token=None, allow_ip=False):
    """True - if checking is not possible right now."""
    if pick_channel(cfg, token, allow_ip) is not None:
        return False
    left = lock_left(cfg, "token") if token else 0
    if left > 0:
        print(red("  account channel is locked") + gray(f"  {fmt_left(left)}"))
        print(gray("  a request during a lock extends it"))
        if pool_alive() and not allow_ip:
            print(gray("  the VPN pool is up: turn on the IP channel in settings"))
    elif pool_alive():
        print(red("  every VPN node is spent"))
        print(gray("  refresh the node list in settings"))
    else:
        print(red("  no token and the IP channel is locked"))
    return True


def report_locks(cfg, stats, allow_ip=False):
    """Prints what is still locked after a run. True - if everything is closed."""
    if stats.locked_until <= time.time():
        return False
    for ch in CHANNELS:
        left = lock_left(cfg, ch)
        if left > 0:
            print(gray(f"  {CHANNEL_NAMES[ch]}  {fmt_left(left)}"))
    if pick_channel(cfg, cfg.get("token"), allow_ip) is None:
        print(red("  nothing left to check with"))
        return True
    print(gray("  the spare channel is free, but it is small"))
    return False

def remember_pace(cfg, stats):
    """Keeps the delay we finished with, so the next run starts there."""
    if stats.final_delay is None or cfg.get("mode") != "safe":
        return
    if stats.checked <= 0:
        return  # a run that never reached the server teaches nothing
    # do not let the delay drift to infinity after a series of locks
    cfg["delay"] = round(max(0.4, min(3.0, stats.final_delay)), 2)
    save_config(cfg)


def show_run_tail(stats, allow_ip=False, cfg=None):
    """Shared footer after a scan: rate, lock state, empty result."""
    elapsed = time.time() - stats.started
    print()
    if stats.net_down:
        print(gray(f"  {stats.checked} attempts in {elapsed:.1f}s"))
    else:
        print(gray(f"  {stats.checked} checks in {elapsed:.1f}s"
                   f"  {stats.checked / max(elapsed, 0.001):.1f}/s"))
    remember_pace(cfg, stats)

    if stats.net_down:
        print(red("  no connection, check your proxy or VPN"))
        return "net"
    if stats.locked_until > time.time():
        print(red("  rate limited"))
        report_locks(cfg, stats, allow_ip)
        return "locked"
    if not stats.free:
        print(yellow("  nothing free"))
        if stats.limits:
            print(gray(f"  small limits: {stats.limits}"))
        return "empty"
    return "free"


def cfg_int(cfg, key, default):
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_float(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def run_auto(cfg):
    clear()
    title("auto pick")
    token = ensure_token(cfg, verbose=False)
    allow_ip = bool(cfg.get("use_ip", False))
    if guard_lock(cfg, token, allow_ip):
        pause()
        return

    limit = ask_int("variants per length [100]: ", 100, 1, 5000)
    min_len = ask_int("min length 3-8 [3]: ", 3, 3, 8)
    # the default must never fall below the minimum, or the range comes out empty
    max_default = max(min_len, 4)
    max_len = ask_int(f"max length {min_len}-8 [{max_default}]: ",
                      max_default, min_len, 8)
    print(f"    {ui.accent('1')} digits   {ui.accent('2')} letters   {ui.accent('3')} mixed")
    mode = ask("> ").strip() or "3"
    alphabet = {"1": string.digits, "2": string.ascii_lowercase}.get(
        mode, string.ascii_lowercase + string.digits
    )
    workers = max(1, cfg_int(cfg, "workers", 1))
    delay = cfg_float(cfg, "delay", MODES["safe"]["delay"])

    names = []
    for length in range(min_len, max_len + 1):
        names += gen_unique(limit, length, alphabet)

    print()
    print(gray(f"  {len(names)} names, {workers} worker(s), {delay}s"))
    print()
    free_name, stats = scan(cfg, token, names, workers, delay, allow_ip)

    tail = show_run_tail(stats, allow_ip, cfg)
    if tail != "free":
        pause()
        return

    print()
    ui.reveal(free_name, green("  free") + gray(f"  {len(free_name)} chars"))
    offer_set(cfg, free_name)
    pause()

def run_file(cfg):
    clear()
    title("check a list")
    path = ask("file, Enter for names.txt: ").strip()
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "names.txt")
    if not os.path.isfile(path):
        print(red(f"  no such file: {path}"))
        pause()
        return

    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        raw = [line.strip().lstrip("@") for line in f]
    names, seen = [], set()
    for n in raw:
        if 2 <= len(n) <= 32 and n not in seen and " " not in n:
            seen.add(n)
            names.append(n)
    if not names:
        print(yellow("  no usable names in that file"))
        pause()
        return

    token = ensure_token(cfg, verbose=False)
    allow_ip = bool(cfg.get("use_ip", False))
    if guard_lock(cfg, token, allow_ip):
        pause()
        return
    workers = max(1, cfg_int(cfg, "workers", 1))
    delay = cfg_float(cfg, "delay", MODES["safe"]["delay"])
    print()
    print(gray(f"  {len(names)} names, {workers} worker(s), {delay}s"))
    print()

    free_name, stats = scan(cfg, token, names, workers, delay, allow_ip)
    tail = show_run_tail(stats, allow_ip, cfg)
    if tail != "free":
        pause()
        return

    print()
    ui.reveal(free_name, green("  free") + gray(f"  {len(free_name)} chars"))
    offer_set(cfg, free_name)
    pause()

def run_manual(cfg):
    clear()
    title("check a name")
    name = ask("name: ").strip()
    if not name:
        return
    token = ensure_token(cfg, verbose=False)
    allow_ip = bool(cfg.get("use_ip", False))
    if guard_lock(cfg, token, allow_ip):
        pause()
        return
    channel = pick_channel(cfg, token, allow_ip)
    client = net.Client()
    spin = ui.Loader(("checking",), width=16, min_time=0.4)
    try:
        status, retry = spin.run(lambda _mark: check_username(client, name, token, channel=channel))
    except Exception:
        status, retry = ERROR, 0
    finally:
        client.close()

    if status == TAKEN:
        print(f"  {label('taken')} {red(name)}")
    elif status == FREE:
        ui.reveal(name, green("  free"))
        offer_set(cfg, name)
    elif status == INVALID:
        print(f"  {label('invalid')} {gray(name)}")
    elif status == LIMITED:
        if retry > HARD_LIMIT:
            set_lock(cfg, retry, channel)
            print(red(f"  {CHANNEL_NAMES[channel]} locked: {fmt_left(retry)}"))
        else:
            print(yellow(f"  small limit, {int(retry) + 1}s"))
    else:
        print(red("  no connection, check your proxy or VPN"))
    pause()

# ---------------- screen ----------------

STAGES = ("config", "discord path", "storage", "token")


def info(cfg):
    """State lines under the logo."""
    if cfg.get("token"):
        print(f"  {label('account')} {green(cfg.get('me') or 'ok')}")
    else:
        print(f"  {label('account')} {red('no token')}")
        if not has_leveldb(normalize_path(cfg.get("discord_path"))):
            print(f"  {label('path')} {yellow('not found')}  {gray('settings -> path')}")

    mode = cfg.get("mode", "safe")
    print(f"  {label('mode')} {ui.accent(MODES.get(mode, MODES['safe'])['name'])}"
          f"  {gray(str(cfg.get('delay', 1.0)) + 's')}")

    vcfg = vpn_settings(cfg)
    if vcfg["enabled"]:
        alive, dead = net.pool_status()
        if alive:
            tail = gray(f"  {dead} dropped") if dead else ""
            print(f"  {label('vpn')} {green(ip_count(alive))}  {gray('rotating')}{tail}")
        else:
            print(f"  {label('vpn')} {yellow('no live nodes')}")

    left_t = lock_left(cfg, "token") if cfg.get("token") else 0
    left_ip = lock_left(cfg, "ip")
    if left_t > 0 or (left_ip > 0 and not pool_alive()):
        parts = []
        if left_t > 0:
            parts.append(f"account {fmt_left(left_t)}")
        if left_ip > 0 and not pool_alive():
            parts.append(f"IP {fmt_left(left_ip)}")
        color = red if left_t > 0 else yellow
        print(f"  {label('lock')} {color(', '.join(parts))}")


# items for the selectable menu: (title, action)
MENU_ITEMS = [
    ("find short names", "auto"),
    ("check one name", "manual"),
    ("check a list", "file"),
    ("settings", "settings"),
    ("quit", "quit"),
]


def _item(title, selected):
    mark = ui.accent("> ") if selected else "  "
    return mark + ui.paint(title, width=max(1, len(title)),
                              span=(0.35, 1.0) if selected else (0.25, 0.95))


def _redraw(items, cur):
    n = len(items)
    sys.stdout.write(f"\x1b[{n}A")
    for i, it in enumerate(items):
        sys.stdout.write("\x1b[K" + _item(it, i == cur) + "\n")
    sys.stdout.flush()


def menu(animate=False):
    """Selectable menu: arrows / W S to move, Enter to pick, Q / Esc to quit."""
    print()
    print("  " + ui.rule(32))
    print()
    items = [entry[0] for entry in MENU_ITEMS]
    count = len(items)
    cur = 0
    for i in range(count):
        print(_item(items[i], i == cur))
        sys.stdout.flush()
        if animate:
            time.sleep(0.045)
    sys.stdout.flush()

    if not _interactive_input():
        choice = input(ui.muted("  > ")).strip().lower()
        print()
        if not choice:
            return MENU_ITEMS[cur][1]
        if choice in ("q", "0", "quit"):
            return "quit"
        for i, (title, action) in enumerate(MENU_ITEMS):
            if choice == str(i + 1) or title.lower() == choice:
                return action
        return MENU_ITEMS[cur][1]

    while True:
        key = _read_key()
        if key == "up":
            cur = (cur - 1) % count
            _redraw(items, cur)
        elif key == "down":
            cur = (cur + 1) % count
            _redraw(items, cur)
        elif key == "enter":
            print()
            return MENU_ITEMS[cur][1]
        elif key == "quit":
            print()
            return "quit"


def screen(cfg):
    """Static base under the menu."""
    print(ui.logo())
    print()
    info(cfg)


def boot_screen(cfg):
    """First screen: logo at once, then the boot animation under it."""
    clear()
    art = ui.logo()
    height = art.count("\n") + 1
    print(art)
    # light sweeps across the logo, then it settles
    ui.sweep(lambda glow: ui.logo(glow), height, seconds=0.75)

    vcfg = vpn_settings(cfg)
    stages = list(STAGES) + (["vpn"] if vcfg["enabled"] else [])
    loader = ui.Loader(tuple(stages))
    note = {"text": ""}
    state = {"path": False}

    def work(mark):
        mark(0)
        mark(1)
        state["path"] = autodetect(cfg)
        mark(2)
        ensure_token(cfg, verbose=False, mark=mark)
        mark(3)
        _pool_obj, msg = start_pool(cfg, mark)
        note["text"] = msg
        mark(len(stages))

    try:
        loader.run(work)
    except Exception as exc:
        note["text"] = red(f"  boot: {exc}")

    print()
    if note["text"]:
        print(note["text"])
        print()

    # the path only matters when a token has to be pulled out of the client,
    # so a working stored token means no questions
    if not state["path"] and not cfg.get("token"):
        if ask_discord_path(cfg):
            ensure_token(cfg)
    info(cfg)

def main():
    enable_colors()
    cfg = load_config()
    try:
        boot_screen(cfg)

        handlers = {
            "auto": run_auto, "manual": run_manual, "file": run_file,
            "settings": settings_menu,
        }
        first = True
        while True:
            try:
                action = menu(animate=first)
            except EOFError:
                break
            first = False
            if action == "quit":
                break
            if action not in handlers:
                continue
            try:
                handlers[action](cfg)
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception as exc:
                # a network or file error must never end with a traceback
                print()
                print(red(f"  {type(exc).__name__}: {exc}"))
                pause()
            clear()
            screen(cfg)
    finally:
        # sing-box must not outlive the program
        stop_pool()

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()

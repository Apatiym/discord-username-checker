# -*- coding: utf-8 -*-
"""Network: keep-alive HTTP client, proxy autodetect, and the VPN pool.

Why http.client: urllib opens a fresh TLS handshake per request (0.2-0.4 s),
connection reuse brings that down to ~70-140 ms.

Why the proxy order matters: on networks where Discord is blocked a direct TLS
handshake does not time out, it fails instantly with WRONG_VERSION_NUMBER - so
trying "direct" first would look fast and always win, breaking everything.

VPN pool: https://github.com/igareck/vpn-configs-for-russia publishes plain
subscription lists (one URI per line). Python cannot speak VLESS / Trojan /
Hysteria2, so sing-box (shipped with Happ) does the tunneling:

    our code -> HTTP proxy 127.0.0.1:PORT -> sing-box outbound -> node -> Discord

Every node gets its own inbound port and its own outbound, wired with a routing
rule, so a worker on port N exits through node N - parallel workers really do
use different IPs.

Why that helps: the IP channel is limited per IP, so a pool of N nodes
multiplies its budget. The account channel is limited per token - no amount of
IPs resets it. That is a Discord-side rule, not a limitation here.
"""

import concurrent.futures
import http.client
import json
import os
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse

HOST = "discord.com"
PORT = 443
LOCAL_PORTS = ("http://127.0.0.1:10809", "http://127.0.0.1:7890", "http://127.0.0.1:1080")

_proxy_lock = threading.Lock()
# value: None (unknown / dead), "direct", or "http://host:port"
# at:    when the last detection finished, so a failure is not retried in a loop
_proxy_cache = {"value": None, "at": 0.0}
FAIL_TTL = 20.0

# optional VPN pool: when set, every worker exits through its own IP
_pool = None


def use_pool(pool):
    """Turns on VPN rotation for every Client created from now on."""
    global _pool
    _pool = pool


def pool_status():
    """(alive, dead) for the interface, or (0, 0) without a pool."""
    return _pool.info() if _pool is not None else (0, 0)


def _registry_proxy():
    """Proxy from the Windows settings (WinINET), if enabled."""
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if not enabled or not server:
            return None
        if "=" in server:  # "http=1.2.3.4:80;https=1.2.3.4:443"
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or ""
        if not server:
            return None
        return server if "://" in server else "http://" + server
    except Exception:
        return None


def _is_http_proxy(value):
    """http.client can tunnel through HTTP proxies only, not SOCKS."""
    if value == "direct":
        return True
    try:
        scheme = urllib.parse.urlsplit(value).scheme.lower()
    except Exception:
        return False
    return scheme in ("http", "https")


def proxy_candidates():
    """Ordered candidates: explicit settings first, then usual ports, direct last."""
    out = []
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(var)
        if v:
            out.append(v if "://" in v else "http://" + v)
    reg = _registry_proxy()
    if reg:
        out.append(reg)
    out.extend(LOCAL_PORTS)
    out.append("direct")

    seen, uniq = set(), []
    for c in out:
        if not _is_http_proxy(c) or c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq


def _detect_proxy(force=False):
    """Finds a route that actually answers. Cached, with a cooldown on failure.

    A total failure is never remembered as "direct": that would pin every later
    request to a dead route (the VPN may just have been starting up).
    """
    with _proxy_lock:
        now = time.time()
        if not force:
            if _proxy_cache["value"] is not None:
                return _proxy_cache["value"]
            if now - _proxy_cache["at"] < FAIL_TTL:
                return None

        working = None
        for proxy in proxy_candidates():
            s = Session(proxy, timeout=4)
            try:
                code, _ = s.request("GET", "/api/v9/gateway")
                if code:
                    working = proxy
                    break
            except Exception:
                pass
            finally:
                s.close()

        _proxy_cache["value"] = working
        _proxy_cache["at"] = now
        return working


class Session:
    """One keep-alive connection. Not thread-safe - one per worker."""

    def __init__(self, proxy=None, timeout=8):
        self.proxy = proxy
        self.timeout = timeout
        self.conn = None
        self.ctx = ssl.create_default_context()

    def _connect(self):
        if self.proxy and self.proxy != "direct":
            p = urllib.parse.urlsplit(self.proxy)
            conn = http.client.HTTPSConnection(
                p.hostname, p.port or 8080, timeout=self.timeout, context=self.ctx
            )
            conn.set_tunnel(HOST, PORT)
        else:
            conn = http.client.HTTPSConnection(
                HOST, PORT, timeout=self.timeout, context=self.ctx
            )
        conn.connect()
        return conn

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def request(self, method, path, body=None, headers=None):
        """Returns (status, dict). Raises only when the network is dead."""
        hdrs = {
            "Host": HOST,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)

        for attempt in (0, 1):
            # a stale keep-alive is worth one retry; a fresh connect failing
            # means the route itself is dead - retrying would just waste time
            reusable = self.conn is not None
            try:
                if self.conn is None:
                    self.conn = self._connect()
                self.conn.request(method, path, body=data, headers=hdrs)
                resp = self.conn.getresponse()
                raw = resp.read()
                if resp.will_close:  # the server closed it - reconnect next time
                    self.close()
                try:
                    parsed = json.loads(raw) if raw else {}
                except Exception:
                    parsed = {"message": raw.decode("utf-8", "ignore")}
                return resp.status, parsed
            except (http.client.HTTPException, OSError):
                self.close()
                if attempt or not reusable:
                    raise
        raise OSError("no connection")


class Client:
    """Session pool: one session per thread, rotating over VPN nodes if asked."""

    def __init__(self, proxy=None, timeout=8, rotate=True):
        self.timeout = timeout
        self._fixed = proxy is not None
        self._pool = _pool if (rotate and proxy is None) else None
        self.proxy = proxy if self._fixed else (None if self._pool else _detect_proxy())
        self._local = threading.local()
        self._all = []
        self._lock = threading.Lock()
        self._gen = 0

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None or getattr(self._local, "gen", -1) != self._gen:
            if self._pool is not None:
                proxy = self._pool.pick()
                if proxy is None:  # the pool burned out - fall back to the usual route
                    proxy = _detect_proxy()
                self._local.proxy = proxy
            else:
                proxy = self.proxy
            s = Session(proxy, self.timeout)
            self._local.session = s
            self._local.gen = self._gen
            with self._lock:
                self._all.append(s)
        return s

    def fail_current(self):
        """Drops the node this thread was using (it is rate limited or dead)."""
        proxy = getattr(self._local, "proxy", None)
        self._drop()
        if self._pool is not None and proxy:
            self._pool.fail(proxy)

    def _drop(self):
        s = getattr(self._local, "session", None)
        if s is not None:
            s.close()
        self._local.session = None

    def request(self, method, path, body=None, headers=None):
        if self._pool is not None:
            # a dead node looks exactly like a dead route, so try the next IP
            for attempt in (0, 1, 2):
                try:
                    return self._session().request(method, path, body, headers)
                except (http.client.HTTPException, OSError):
                    self.fail_current()
                    if attempt == 2:
                        raise
            raise OSError("no connection")

        try:
            return self._session().request(method, path, body, headers)
        except (http.client.HTTPException, OSError):
            if self._fixed:
                raise
            # the route died (VPN restarted, wrong guess) - look again once
            self.close()
            fresh = _detect_proxy(force=True)
            if fresh is None or fresh == self.proxy:
                raise
            self.proxy = fresh
            with self._lock:
                self._gen += 1
            return self._session().request(method, path, body, headers)

    def close(self):
        with self._lock:
            for s in self._all:
                s.close()
            self._all.clear()
            self._gen += 1
        self._local.session = None


# ================= VPN pool =================

RAW = "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/"

# lists published by the repo; order = preference
SOURCES = (
    "BLACK_VLESS_RUS.txt",
    "Vless-Reality-White-Lists-Rus-Mobile.txt",
    "BLACK_VLESS_RUS_mobile.txt",
    "BLACK_SS+All_RUS.txt",
)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpns.json")
CACHE_TTL = 6 * 3600
GOOD_TTL = 3 * 24 * 3600  # a node dead for this long is forgotten

SINGBOX_PATHS = (
    r"C:\Program Files\FlyFrogLLC\Happ\tun\sing-box.exe",
    r"C:\Program Files\Happ\tun\sing-box.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Happ", "tun", "sing-box.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Happ", "tun", "sing-box.exe"),
)

# used only to prove a node is alive and to read its egress IP
PROBE_HOST = "api.ipify.org"


def find_singbox():
    for path in SINGBOX_PATHS:
        if path and os.path.isfile(path):
            return path
    for name in ("sing-box.exe", "sing-box"):
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                return cand
    return None


# ---------------- parsing ----------------

def _q(query):
    """Query string -> dict, first value wins, percent-decoded."""
    out = {}
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if key not in out:
            out[key] = value
    return out


def _name(fragment, fallback):
    raw = urllib.parse.unquote(fragment or "").strip()
    # node names are full of emoji flags; keep something printable
    clean = "".join(ch for ch in raw if ch.isprintable()).strip()
    return clean or fallback


def parse_uri(uri):
    """One subscription URI -> normalized node dict, or None if unsupported."""
    uri = uri.strip()
    if "://" not in uri:
        return None
    scheme, body = uri.split("://", 1)
    try:
        if scheme == "vless":
            return _parse_vless(body)
        if scheme == "trojan":
            return _parse_trojan(body)
        if scheme in ("hysteria2", "hy2"):
            return _parse_hy2(body)
    except Exception:
        return None
    return None


def _split_host(body):
    """'uuid@host:port?query#name' -> (userinfo, host, port, params, name)."""
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    query = ""
    if "?" in body:
        body, query = body.split("?", 1)
    if "@" not in body:
        raise ValueError("no userinfo")
    userinfo, hostport = body.rsplit("@", 1)
    host, _, port = hostport.rpartition(":")
    if not host:
        host, port = hostport, "443"
    return userinfo, host.strip("[]"), int(port), _q(query), fragment


def _parse_vless(body):
    uuid, host, port, params, fragment = _split_host(body)
    node = {
        "proto": "vless",
        "uuid": uuid,
        "server": host,
        "port": port,
        "name": _name(fragment, f"{host}:{port}"),
    }
    security = params.get("security", "none").lower()
    if security in ("tls", "reality"):
        tls = {"enabled": True}
        sni = params.get("sni") or params.get("host") or host
        if sni:
            tls["server_name"] = sni
        if params.get("fp"):
            tls["utls"] = {"enabled": True, "fingerprint": params["fp"]}
        if params.get("alpn"):
            tls["alpn"] = [a for a in params["alpn"].split(",") if a]
        if security == "reality":
            pbk = params.get("pbk")
            if not pbk:
                return None  # reality without a public key cannot work
            tls["reality"] = {"enabled": True, "public_key": pbk,
                              "short_id": params.get("sid", "")}
        if params.get("allowInsecure") == "1":
            tls["insecure"] = True
        node["tls"] = tls

    if params.get("flow"):
        node["flow"] = params["flow"]

    transport = _transport(params)
    if transport:
        node["transport"] = transport
    return node


def _parse_trojan(body):
    password, host, port, params, fragment = _split_host(body)
    node = {
        "proto": "trojan",
        "password": password,
        "server": host,
        "port": port,
        "name": _name(fragment, f"{host}:{port}"),
        "tls": {"enabled": True},
    }
    node["tls"]["server_name"] = params.get("sni") or params.get("peer") or host
    if params.get("fp"):
        node["tls"]["utls"] = {"enabled": True, "fingerprint": params["fp"]}
    if params.get("alpn"):
        node["tls"]["alpn"] = [a for a in params["alpn"].split(",") if a]
    if params.get("allowInsecure") == "1":
        node["tls"]["insecure"] = True
    transport = _transport(params)
    if transport:
        node["transport"] = transport
    return node


def _parse_hy2(body):
    auth, host, port, params, fragment = _split_host(body)
    node = {
        "proto": "hysteria2",
        "password": auth,
        "server": host,
        "port": port,
        "name": _name(fragment, f"{host}:{port}"),
        "tls": {"enabled": True, "server_name": params.get("sni") or host},
    }
    if params.get("insecure") == "1":
        node["tls"]["insecure"] = True
    if params.get("alpn"):
        node["tls"]["alpn"] = [a for a in params["alpn"].split(",") if a]
    if params.get("obfs") == "salamander" and params.get("obfs-password"):
        node["obfs"] = {"type": "salamander", "password": params["obfs-password"]}
    return node


def _transport(params):
    """VLESS/Trojan transport -> sing-box transport block."""
    kind = (params.get("type") or "tcp").lower()
    if kind in ("ws", "websocket"):
        ws = {"type": "ws", "path": params.get("path") or "/"}
        if params.get("host"):
            ws["headers"] = {"Host": params["host"]}
        return ws
    if kind == "grpc":
        return {"type": "grpc", "service_name": params.get("serviceName", "")}
    if kind in ("http", "h2"):
        block = {"type": "http", "path": params.get("path") or "/"}
        if params.get("host"):
            block["host"] = [h for h in params["host"].split(",") if h]
        return block
    if kind in ("tcp", "none", ""):
        if params.get("headerType") == "http":
            return {"type": "http", "path": params.get("path") or "/"}
        return None
    return None  # quic/kcp are not supported by sing-box for vless


def parse_subscription(text):
    """A whole list file -> unique nodes."""
    nodes, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        node = parse_uri(line)
        if not node:
            continue
        key = _node_key(node)
        if key in seen:
            continue
        seen.add(key)
        nodes.append(node)
    return nodes


# ---------------- download / cache ----------------

def _fetch(url, timeout=12):
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection("raw.githubusercontent.com", timeout=timeout, context=ctx)
    try:
        path = url.split("raw.githubusercontent.com", 1)[1]
        conn.request("GET", path, headers={"User-Agent": "curl/8"})
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status}")
        return raw.decode("utf-8", "ignore")
    finally:
        conn.close()


def _read_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(data):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load_cached_nodes():
    data = _read_cache()
    if time.time() - data.get("at", 0) > CACHE_TTL:
        return None
    nodes = data.get("nodes")
    return nodes if isinstance(nodes, list) and nodes else None


def _node_key(node):
    return (node["proto"], node["server"], node["port"],
            node.get("uuid") or node.get("password"))


def _key_str(key):
    return "|".join(str(part) for part in key)


def load_good():
    """Keys of nodes that passed traffic recently, freshest first."""
    good = _read_cache().get("good")
    if not isinstance(good, dict):
        return []
    now = time.time()
    fresh = {k: float(v) for k, v in good.items() if now - float(v) < GOOD_TTL}
    return [k for k, _ in sorted(fresh.items(), key=lambda kv: kv[1], reverse=True)]


def save_good(nodes):
    """Merges the alive nodes into the history instead of replacing it.

    A single unlucky start (all sampled nodes dead) must not wipe what we
    learned about the good ones - that would make every run cold.
    """
    data = _read_cache()
    now = time.time()
    good = data.get("good")
    if not isinstance(good, dict):
        good = {}
    # decay: drop entries that have not been seen alive for a while
    good = {k: float(v) for k, v in good.items() if now - float(v) < GOOD_TTL}
    for node in nodes:
        good[_key_str(_node_key(node))] = now
    data["good"] = good
    _write_cache(data)


def order_by_history(nodes):
    """Known-good nodes first - a warm start probes 14 instead of 58."""
    good = set(load_good())
    if not good:
        return list(nodes)
    fresh = [n for n in nodes if _key_str(_node_key(n)) in good]
    rest = [n for n in nodes if _key_str(_node_key(n)) not in good]
    return fresh + rest


def load_nodes(refresh=False, log=None):
    """All nodes from the repo, cached for 6 hours. [] when nothing works."""
    if not refresh:
        cached = _load_cached_nodes()
        if cached:
            return cached

    nodes = []
    for name in SOURCES:
        try:
            text = _fetch(RAW + urllib.parse.quote(name))
        except Exception as exc:
            if log:
                log(f"  {name}: {exc}")
            continue
        part = parse_subscription(text)
        if log:
            log(f"  {name}: {len(part)} nodes")
        nodes.extend(part)

    seen, unique = set(), []
    for node in nodes:
        key = _node_key(node)
        if key not in seen:
            seen.add(key)
            unique.append(node)

    if unique:
        data = _read_cache()
        data["at"] = time.time()
        data["nodes"] = unique
        _write_cache(data)
    return unique


# ---------------- sing-box ----------------

def _free_port(start=20000, tries=400):
    """A port nothing listens on right now.

    No SO_REUSEADDR: on Windows it lets a socket bind a port that is already
    taken, so the check would hand out busy ports and sing-box would fail.
    """
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError("no free port")


def _alloc_ports(count, start=20000):
    """`count` distinct free ports; one scan, so they cannot collide."""
    ports, used = [], set()
    port = start
    while len(ports) < count:
        if port > start + 2000:
            raise OSError("no free port range")
        if port not in used:
            try:
                candidate = _free_port(port, tries=1)
            except OSError:
                candidate = None
            if candidate is not None:
                ports.append(candidate)
                used.add(candidate)
        port += 1
    return ports


def outbound(node, tag):
    """Normalized node -> sing-box outbound."""
    base = {"type": node["proto"], "tag": tag,
            "server": node["server"], "server_port": node["port"]}
    if node["proto"] == "vless":
        base["uuid"] = node["uuid"]
        if node.get("flow"):
            base["flow"] = node["flow"]
    else:
        base["password"] = node["password"]
    for key in ("tls", "transport", "obfs"):
        if node.get(key):
            base[key] = dict(node[key])
    return base


def build_config(nodes, ports):
    """One config: inbound port i is routed to outbound i."""
    inbounds, outbounds, rules = [], [], []
    for i, node in enumerate(nodes):
        inbounds.append({"type": "http", "tag": f"in-{i}",
                         "listen": "127.0.0.1", "listen_port": ports[i]})
        outbounds.append(outbound(node, f"out-{i}"))
        rules.append({"inbound": [f"in-{i}"], "outbound": f"out-{i}"})
    outbounds.append({"type": "direct", "tag": "direct"})
    return {
        "log": {"level": "error", "timestamp": False},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules, "final": "direct"},
    }


def probe(proxy, timeout=5.0):
    """Reads the egress IP through a local proxy. None when the node is dead."""
    host, port = proxy.rsplit(":", 1)
    host = host.replace("http://", "")
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, int(port), timeout=timeout, context=ctx)
    try:
        conn.set_tunnel(PROBE_HOST, 443)
        conn.request("GET", "/", headers={"Host": PROBE_HOST, "User-Agent": "curl/8"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "ignore").strip()
        if resp.status == 200 and 7 <= len(body) <= 64 and " " not in body:
            return body
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


class Pool:
    """Runs one sing-box with N nodes and hands out working local proxies."""

    def __init__(self, nodes, size=12, log=None):
        self.nodes = list(nodes)
        self.size = max(1, min(size, len(self.nodes)))
        self.log = log or (lambda *a: None)
        self.proc = None
        self.ports = []
        self.proxies = []          # working ones, best latency first
        self.egress = {}           # proxy -> egress IP
        self.dead = set()
        self._lock = threading.Lock()
        self._rr = 0
        self.config_path = None

    def start(self, sample=None):
        """Starts sing-box on a batch of nodes and keeps the live ones.

        A cold start has no history, so a small batch can land on dead nodes
        only. When that happens the batch is widened once instead of giving up.
        """
        binary = find_singbox()
        if not binary:
            raise RuntimeError("sing-box not found (install Happ or sing-box)")
        if not self.nodes:
            raise RuntimeError("no VPN nodes parsed")

        ordered = order_by_history(self.nodes)
        first = sample or max(self.size * 3, 12)
        last_error = ""
        tried = set()
        for cap in (first, len(ordered)):
            cap = min(cap, len(ordered))
            if cap in tried:
                continue
            tried.add(cap)
            try:
                return self._boot(ordered[:cap], binary)
            except RuntimeError as exc:
                last_error = str(exc)
        raise RuntimeError(last_error or "no node answered")

    def _boot(self, chosen, binary):
        """One sing-box run on `chosen`; keeps the nodes that answer."""
        ports = _alloc_ports(len(chosen))

        tmpdir = os.path.join(os.path.dirname(CACHE), ".vpn")
        os.makedirs(tmpdir, exist_ok=True)
        self.config_path = os.path.join(tmpdir, "singbox.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(build_config(chosen, ports), f, indent=1)

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [binary, "run", "-c", self.config_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=flags,
        )

        candidates = [f"http://127.0.0.1:{p}" for p in ports]
        alive = self._healthcheck(candidates)
        if self.proc.poll() is not None:
            err = self._read_error()
            self.stop()
            raise RuntimeError(f"sing-box exited{': ' + err if err else ''}")
        if not alive:
            self.stop()
            raise RuntimeError("no node answered")

        alive.sort(key=lambda item: item[1])
        self.proxies = [p for p, _ip, _ms in alive[: self.size]]
        self.egress = {p: ip for p, ip, _ms in alive}
        self.ports = ports
        save_good([chosen[ports.index(int(p.rsplit(":", 1)[1]))] for p in self.proxies])
        self.log(f"  {len(self.proxies)} of {len(candidates)} nodes alive")
        return len(self.proxies)

    def _read_error(self):
        """Last stderr line from sing-box, without ever blocking on a live one."""
        if self.proc is None or self.proc.stderr is None:
            return ""
        try:
            _out, err = self.proc.communicate(timeout=2)
        except Exception:
            return ""
        data = (err or b"").decode("utf-8", "ignore").strip()
        return data.splitlines()[-1][:200] if data else ""

    def _healthcheck(self, proxies, timeout=6.0):
        """Probes every inbound in parallel; returns [(proxy, ip, ms)]."""
        time.sleep(1.2)  # let the listeners come up
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, len(proxies))) as pool:
            futures = {pool.submit(probe, p, timeout): p for p in proxies}
            for fut in concurrent.futures.as_completed(futures):
                proxy = futures[fut]
                started = time.time()
                try:
                    ip = fut.result()
                except Exception:
                    ip = None
                if ip:
                    results.append((proxy, ip, time.time() - started))
        return results

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.proxies = []
        self.egress = {}

    def alive(self):
        return bool(self.proxies)

    def pick(self):
        """Next live proxy, round-robin. None when the pool is empty."""
        with self._lock:
            if not self.proxies:
                return None
            self._rr = (self._rr + 1) % len(self.proxies)
            return self.proxies[self._rr]

    def fail(self, proxy):
        """A node stopped answering: drop it from the rotation."""
        with self._lock:
            self.dead.add(proxy)
            self.proxies = [p for p in self.proxies if p not in self.dead]

    def info(self):
        with self._lock:
            return len(self.proxies), len(self.dead)

#!/usr/bin/env python3
"""Explicit TCP/UDP IPv4 acceptance probes with bounded test server lifetime."""
import argparse
import json
import socket
import threading
import time


def ports(value):
    try:
        values = sorted(set(int(part) for part in value.split(',')))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('use comma-separated port numbers') from exc
    if not values or len(values) > 64 or any(not 1 <= p <= 65535 for p in values):
        raise argparse.ArgumentTypeError('supply 1 to 64 ports within 1..65535')
    return values


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def start_servers(bind, port_list, marker):
    sockets = []
    stop = threading.Event()
    threads = []
    try:
        # Bind every socket before reporting success; fail atomically on collision.
        for port in port_list:
            for proto in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                sock = socket.socket(socket.AF_INET, proto)
                sockets.append(sock)
                if proto == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((bind, port))
                sock.settimeout(0.25)
                if proto == socket.SOCK_STREAM:
                    sock.listen(8)
        def worker(sock):
            while not stop.is_set():
                try:
                    if sock.type == socket.SOCK_STREAM:
                        conn, _ = sock.accept()
                        with conn:
                            conn.settimeout(1)
                            request = b''
                            deadline = time.monotonic() + 1
                            while len(request) < 6:
                                left = deadline - time.monotonic()
                                if left <= 0:
                                    break
                                conn.settimeout(left)
                                block = conn.recv(6 - len(request))
                                if not block:
                                    break
                                request += block
                            if request == b'probe\n':
                                conn.sendall(marker)
                    else:
                        data, addr = sock.recvfrom(64)
                        if data == b'probe\n':
                            sock.sendto(marker, addr)
                except socket.timeout:
                    continue
                except OSError:
                    if stop.is_set():
                        break
        for sock in sockets:
            thread = threading.Thread(target=worker, args=(sock,), daemon=True)
            thread.start()
            threads.append(thread)
    except Exception:
        stop.set()
        for sock in sockets:
            sock.close()
        raise
    def close():
        stop.set()
        for sock in sockets:
            sock.close()
        for thread in threads:
            thread.join(timeout=2)
    return close


def probe(host, port_list, marker, timeout, repeats):
    results = []
    for port in port_list:
        for label, proto in (('TCP', socket.SOCK_STREAM), ('UDP', socket.SOCK_DGRAM)):
            good = 0
            errors = []
            for _ in range(repeats):
                with socket.socket(socket.AF_INET, proto) as sock:
                    sock.settimeout(timeout)
                    try:
                        sock.connect((host, port))
                        sock.sendall(b'probe\n')
                        if proto == socket.SOCK_STREAM:
                            data = b''
                            deadline = time.monotonic() + timeout
                            while len(data) < len(marker):
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    raise TimeoutError('receive deadline exceeded')
                                sock.settimeout(remaining)
                                block = sock.recv(len(marker) - len(data))
                                if not block:
                                    break
                                data += block
                        else:
                            data = sock.recv(1024)
                        if data != marker:
                            raise ValueError('unexpected marker')
                        good += 1
                    except (OSError, ValueError) as exc:
                        errors.append(str(exc))
            results.append({'protocol': label, 'port': port, 'received': good,
                            'attempts': repeats, 'errors': errors})
    return results


def selftest():
    # Reserve an ephemeral candidate then bind TCP+UDP; retry a local race only.
    for _ in range(10):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        try:
            close = start_servers('127.0.0.1', [port], b'local-test')
            break
        except OSError:
            continue
    else:
        raise RuntimeError('unable to allocate local test port')
    try:
        good = probe('127.0.0.1', [port], b'local-test', 1, 2)
        bad = probe('127.0.0.1', [port], b'wrong', 1, 1)
        assert all(r['received'] == 2 for r in good), good
        assert all(r['received'] == 0 for r in bad), bad
    finally:
        close()
    absent = probe('127.0.0.1', [port], b'local-test', 1, 1)
    assert all(r['received'] == 0 for r in absent), absent
    print(json.dumps({'selftest': 'PASS', 'checks': 6}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='mode')
    subs.add_parser('selftest')
    for mode in ('serve', 'client'):
        sub = subs.add_parser(mode)
        sub.add_argument('--ports', type=ports, required=True)
        sub.add_argument('--marker', required=True)
        if mode == 'serve':
            sub.add_argument('--bind', required=True)
            sub.add_argument('--lifetime', type=positive, default=180)
        else:
            sub.add_argument('--host', required=True)
            sub.add_argument('--timeout', type=positive, default=4)
            sub.add_argument('--repeats', type=positive, default=1)
    args = parser.parse_args()
    if not args.mode:
        parser.error('choose serve, client or selftest')
    if args.mode == 'selftest':
        selftest()
        return 0
    marker = args.marker.encode('utf-8')
    if not 1 <= len(marker) <= 512:
        parser.error('marker must be 1..512 UTF-8 bytes')
    if args.mode == 'serve':
        close = start_servers(args.bind, args.ports, marker)
        print(json.dumps({'listening': args.bind, 'ports': args.ports,
                          'lifetime': args.lifetime}), flush=True)
        try:
            time.sleep(args.lifetime)
        except KeyboardInterrupt:
            pass
        finally:
            close()
        return 0
    results = probe(args.host, args.ports, marker, args.timeout, args.repeats)
    print(json.dumps(results, ensure_ascii=False))
    return 0 if all(r['received'] == r['attempts'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bounded zero-payload HTTP throughput probe; never modifies firewall state."""
import argparse
import http.client
import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

SIZE = 16 * 1024 * 1024
CHUNK = bytes(64 * 1024)


class ProbeHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def make_server(bind, port, size):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            if self.path != '/speed-test':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(size))
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            remaining = size
            try:
                while remaining:
                    block = CHUNK[:min(remaining, len(CHUNK))]
                    self.wfile.write(block)
                    remaining -= len(block)
            except (OSError, TimeoutError):
                pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, *_):
            pass

    server = ProbeHTTPServer((bind, port), Handler)
    return server


def download(url, expected_bytes, timeout):
    start = time.monotonic()
    result = {'bytes': 0, 'expected_bytes': expected_bytes, 'complete': False}
    conn = None
    response = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('URL must be http(s) with a hostname')
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError('credentials and fragments are not supported')
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=min(10, timeout))
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        conn.request('GET', path, headers={'Accept-Encoding': 'identity'})
        transport = conn.sock

        def remaining():
            seconds = timeout - (time.monotonic() - start)
            if seconds <= 0:
                raise TimeoutError('total download deadline exceeded')
            return seconds

        transport.settimeout(remaining())
        response = conn.getresponse()
        result['status'] = response.status
        result['first_byte_seconds'] = round(time.monotonic() - start, 6)
        if response.status != 200:
            raise ValueError('HTTP status is not 200')
        if response.getheader('Content-Encoding', 'identity') != 'identity':
            raise ValueError('compressed content invalidates this test')
        if response.getheader('Content-Length') != str(expected_bytes):
            raise ValueError('Content-Length does not match expected bytes')
        while result['bytes'] < expected_bytes:
            transport.settimeout(remaining())
            block = response.read1(min(len(CHUNK), expected_bytes - result['bytes']))
            if not block:
                raise ValueError('incomplete download')
            result['bytes'] += len(block)
            if block.strip(b'\x00'):
                raise ValueError('unexpected payload: expected zero bytes')
        result['complete'] = True
    except (OSError, ValueError, http.client.HTTPException) as exc:
        result['error'] = str(exc)
    finally:
        if response:
            response.close()
        if conn:
            conn.close()
    elapsed = time.monotonic() - start
    result['seconds'] = round(elapsed, 6)
    result['Mbps'] = round(result['bytes'] * 8 / max(elapsed, 1e-9) / 1_000_000, 3)
    return result


def selftest():
    size = 65537  # Exercise the final partial chunk.
    server = make_server('127.0.0.1', 0, size)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = 'http://127.0.0.1:{}/speed-test'.format(server.server_port)
        good = download(url, size, 5)
        bad = download(url, size + 1, 5)
        missing = download(url + '-missing', size, 5)
        assert good['complete'] and good['bytes'] == size, good
        assert not bad['complete'] and 'Content-Length' in bad['error'], bad
        assert not missing['complete'] and missing['status'] == 404, missing
        assert not download(url, size, 0)['complete']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    # Actual truncated, corrupted and stalled HTTP responses, not wording checks.
    for body, declared, delay in ((b'\x00\x00', 10, 0), (b'x', 1, 0), (b'', 10, 0.2)):
        class Fault(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(1)
                self.request.recv(4096)
                headers = 'HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n'.format(declared)
                self.request.sendall(headers.encode() + body)
                time.sleep(delay)
        fixture = socketserver.TCPServer(('127.0.0.1', 0), Fault)
        worker = threading.Thread(target=fixture.handle_request, daemon=True)
        worker.start()
        try:
            result = download('http://127.0.0.1:{}/speed-test'.format(fixture.server_address[1]),
                              declared, 0.05 if delay else 2)
            assert not result['complete'], result
            if delay:
                assert result['seconds'] < 0.2, result
            elif body == b'x':
                assert 'unexpected payload' in result['error'], result
            else:
                assert result['bytes'] == 2, result
        finally:
            worker.join(timeout=2)
            fixture.server_close()
    print(json.dumps({'selftest': 'PASS', 'checks': 7}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='mode')
    serve = subs.add_parser('serve')
    serve.add_argument('--bind', required=True)
    serve.add_argument('--port', type=positive, required=True)
    serve.add_argument('--bytes', type=positive, default=SIZE)
    serve.add_argument('--lifetime', type=positive, default=300)
    client = subs.add_parser('download')
    client.add_argument('--url', required=True)
    client.add_argument('--expected-bytes', type=positive, default=SIZE)
    client.add_argument('--timeout', type=positive, default=120)
    subs.add_parser('selftest')
    args = parser.parse_args()
    if not args.mode:
        parser.error('choose serve, download or selftest')
    if args.mode == 'selftest':
        selftest()
        return 0
    if args.mode == 'download':
        result = download(args.url, args.expected_bytes, args.timeout)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['complete'] else 1
    server = make_server(args.bind, args.port, args.bytes)
    timer = threading.Timer(args.lifetime, server.shutdown)
    timer.daemon = True
    timer.start()
    print(json.dumps({'listening': server.server_address, 'bytes': args.bytes,
                      'lifetime': args.lifetime}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

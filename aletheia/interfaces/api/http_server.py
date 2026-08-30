"""LocalThreadingHTTPServer, extracted from server.py."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

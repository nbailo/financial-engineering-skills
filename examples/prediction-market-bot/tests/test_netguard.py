"""The guard itself. If these fail, nothing else in this directory proves it runs offline."""
import socket
import unittest
import urllib.request

from tests import netguard


class NetworkIsDenied(unittest.TestCase):
    def test_install_is_idempotent(self):
        self.assertFalse(netguard.install(), "the guard was not already installed")

    def test_opening_an_inet_socket_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def test_opening_an_inet6_socket_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM)

    def test_create_connection_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            socket.create_connection(("127.0.0.1", 9))

    def test_name_resolution_is_denied(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            socket.getaddrinfo("localhost", 80)

    def test_an_http_client_cannot_reach_anything(self):
        with self.assertRaises(netguard.NetworkAccessDenied):
            urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)

    def test_the_example_imports_no_http_client(self):
        import fake_venue
        import safe_bot
        import unsafe_bot

        for module in (fake_venue, safe_bot, unsafe_bot):
            source = open(module.__file__, encoding="utf-8").read()
            for forbidden in ("import requests", "import urllib", "import http",
                              "import socket", "websocket", "aiohttp"):
                self.assertNotIn(forbidden, source,
                                 f"{module.__name__} reaches for {forbidden}")


if __name__ == "__main__":
    unittest.main()

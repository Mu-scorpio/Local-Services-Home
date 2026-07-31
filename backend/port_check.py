import socket

# Localhost probes should fail fast; 0.5s per service multiplies under polling.
_DEFAULT_TIMEOUT = 0.12


def is_port_open(host: str, port: int, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Return True if TCP connect to host:port succeeds."""
    if not port or port < 1 or port > 65535:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_local_port(port: int, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    return is_port_open("127.0.0.1", port, timeout=timeout)

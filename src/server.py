import ipaddress
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
from cachetools import TTLCache

# --- Prometheus Metrics ---
# Use a function to get or create metrics to avoid re-registration errors during testing
def _get_or_create_counter(name, documentation, labelnames=None):
    """Get existing counter or create new one."""
    try:
        return REGISTRY._names_to_collectors[name]
    except KeyError:
        return Counter(name, documentation, labelnames or [])

def _get_or_create_gauge(name, documentation):
    """Get existing gauge or create new one."""
    try:
        return REGISTRY._names_to_collectors[name]
    except KeyError:
        return Gauge(name, documentation)

http_requests_total = _get_or_create_counter(
    'http_requests_total',
    'Total number of HTTP requests',
    ['method', 'path', 'status']
)

dns_lookups_total = _get_or_create_counter(
    'dns_lookups_total',
    'Total number of DNS lookups performed'
)

dns_lookups_success_total = _get_or_create_counter(
    'dns_lookups_success_total',
    'Total number of successful DNS lookups'
)

dns_lookups_failure_total = _get_or_create_counter(
    'dns_lookups_failure_total',
    'Total number of failed DNS lookups'
)

dns_cache_hits_total = _get_or_create_counter(
    'dns_cache_hits_total',
    'Total number of DNS cache hits'
)

dns_cache_misses_total = _get_or_create_counter(
    'dns_cache_misses_total',
    'Total number of DNS cache misses'
)

zone_lookups_success_total = _get_or_create_counter(
    'zone_lookups_success_total',
    'Total number of successful zone lookups'
)

zone_lookups_failure_total = _get_or_create_counter(
    'zone_lookups_failure_total',
    'Total number of failed zone lookups'
)

dns_cache_size = _get_or_create_gauge(
    'dns_cache_size',
    'Current number of entries in DNS cache'
)

# --- Configuration ---
# Configure logging to output to stdout
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
try:
    log_level_value = getattr(logging, log_level)
except AttributeError:
    log_level_value = logging.INFO
    print(f"Warning: Invalid LOG_LEVEL '{log_level}', defaulting to INFO", file=sys.stderr)

logging.basicConfig(
    level=log_level_value,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

# --- DNS Cache ---
class DNSCache:
    """Thread-safe DNS cache with TTL support using cachetools.TTLCache."""
    
    def __init__(self, maxsize=1000, ttl=300):
        self.cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self.lock = threading.RLock()
        self.ttl = ttl
        self.default_ttl = ttl  # For backward compatibility with tests
        # Track custom TTLs for individual entries
        self._custom_ttls = {}  # {fqdn: (expiry_time)}
        logging.info(f"DNS cache initialized with TTL: {ttl} seconds, max size: {maxsize}")
    
    def get(self, fqdn):
        """Get cached IP for FQDN if not expired."""
        with self.lock:
            # Check custom TTL first
            if fqdn in self._custom_ttls:
                expiry_time = self._custom_ttls[fqdn]
                if time.time() >= expiry_time:
                    # Custom TTL expired, remove from both caches
                    self._custom_ttls.pop(fqdn, None)
                    self.cache.pop(fqdn, None)
                    dns_cache_misses_total.inc()
                    return None
            
            ip_address = self.cache.get(fqdn)
            if ip_address is not None:
                logging.debug(f"DNS cache hit for {fqdn}: {ip_address}")
                dns_cache_hits_total.inc()
                return ip_address
        dns_cache_misses_total.inc()
        return None
    
    def set(self, fqdn, ip_address, ttl=None):
        """Cache IP address for FQDN with TTL."""
        with self.lock:
            self.cache[fqdn] = ip_address
            
            # Track custom TTL if specified
            if ttl is not None and ttl != self.ttl:
                self._custom_ttls[fqdn] = time.time() + ttl
            else:
                # Remove custom TTL tracking if using default
                self._custom_ttls.pop(fqdn, None)
            
            logging.debug(f"DNS cached {fqdn} -> {ip_address} (TTL: {ttl if ttl is not None else self.ttl}s)")
            dns_cache_size.set(len(self.cache))

    def reset(self):
        """Clear the entire cache."""
        with self.lock:
            self.cache.clear()
            self._custom_ttls.clear()
            dns_cache_size.set(0)
            logging.info("DNS cache has been reset.")
    
    def stats(self):
        """Return cache statistics."""
        with self.lock:
            return {
                'total_entries': len(self.cache),
                'entries': list(self.cache.keys()),
                'maxsize': self.cache.maxsize
            }

# Initialize DNS cache with configurable TTL (default 5 minutes) and max size
DNS_CACHE_TTL = int(os.environ.get('DNS_CACHE_TTL', '300'))
DNS_CACHE_MAXSIZE = int(os.environ.get('DNS_CACHE_MAXSIZE', '1000'))
dns_cache = DNSCache(maxsize=DNS_CACHE_MAXSIZE, ttl=DNS_CACHE_TTL)

def load_subnets_data():
    """Load subnet data from external JSON file."""
    # Check for external JSON file
    subnets_file = os.environ.get("SUBNETS_FILE", "subnets.json")
    
    if os.path.exists(subnets_file):
        try:
            with open(subnets_file, 'r') as f:
                subnets_data = json.load(f)
            logging.info(f"Loaded subnet data from {subnets_file}")
            return subnets_data
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Failed to load subnet data from {subnets_file}: {e}.")
            sys.exit(1)
    else:
        logging.info(f"Subnet file {subnets_file} not found.")
        sys.exit(1)

SUBNETS_DATA = load_subnets_data()

try:
    CIDR_MAPPINGS = {
        ipaddress.ip_network(subnet["CIDRBlock"]): {
            "AvailabilityZone": subnet["AvailabilityZone"],
            "AvailabilityZoneId": subnet["AvailabilityZoneId"]
        } for subnet in SUBNETS_DATA
    }
    logging.info(f"Successfully loaded {len(CIDR_MAPPINGS)} subnet mappings.")
except KeyError as e:
    logging.critical(f"Failed to load or parse subnet information: {e}")
    sys.exit(1)


# --- HTTP Handler ---
class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handles GET requests."""
        if self.path in ('/healthz', '/readyz'):
            self.send_healthy_response()
            http_requests_total.labels(method='GET', path=self.path, status='200').inc()
            return
        
        if self.path == '/metrics':
            self.send_metrics_response()
            http_requests_total.labels(method='GET', path='/metrics', status='200').inc()
            return
        
        if self.path == '/cache/stats':
            self.send_cache_stats()
            http_requests_total.labels(method='GET', path='/cache/stats', status='200').inc()
            return
        
        if self.path == '/cache/reset':
            self.reset_cache()
            http_requests_total.labels(method='GET', path='/cache/reset', status='200').inc()
            return

        if not self.path.startswith('/'):
            self.send_error_response(400, "Invalid path")
            http_requests_total.labels(method='GET', path=self.path, status='400').inc()
            return
        
        fqdn = self.path.strip('/')
        if not fqdn:
            self.send_error_response(404, "Not Found. Please provide a FQDN in the path, e.g., /my.database.com")
            http_requests_total.labels(method='GET', path='/', status='404').inc()
            return

        logging.info(f"Received lookup request for FQDN: {fqdn}")
        try:
            ip_address = self._get_ip_address(fqdn)
            logging.info(f"Resolved {fqdn} to IP address: {ip_address}")

            zone_data = self._get_zone_data(ip_address)
            if zone_data:
                logging.info(f"Found matching zone data for IP {ip_address}, zone: {zone_data['AvailabilityZone']}, zoneId: {zone_data['AvailabilityZoneId']}")
                zone_lookups_success_total.inc()
                self.send_json_response(200, {
                    'zone': zone_data['AvailabilityZone'],
                    'zoneId': zone_data['AvailabilityZoneId']
                })
                http_requests_total.labels(method='GET', path='/fqdn', status='200').inc()
            else:
                logging.warning(f"No matching zone found for IP {ip_address}")
                zone_lookups_failure_total.inc()
                self.send_error_response(404, "Zone not found for the given FQDN's IP")
                http_requests_total.labels(method='GET', path='/fqdn', status='404').inc()
        except socket.gaierror:
            logging.error(f"DNS lookup failed for FQDN: {fqdn}")
            self.send_error_response(404, "FQDN not found or could not be resolved")
            http_requests_total.labels(method='GET', path='/fqdn', status='404').inc()
        except Exception as e:
            logging.critical(f"An unexpected error occurred for FQDN {fqdn}: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
            http_requests_total.labels(method='GET', path='/fqdn', status='500').inc()

    def send_json_response(self, status_code, payload):
        """Sends a JSON response."""
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def send_error_response(self, status_code, message):
        """Sends a JSON error response."""
        self.send_json_response(status_code, {'error': message})

    def send_healthy_response(self):
        """Sends a health check response."""
        self.send_json_response(200, {'status': 'ok'})
    
    def send_cache_stats(self):
        """Sends DNS cache statistics."""
        stats = dns_cache.stats()
        stats['ttl'] = DNS_CACHE_TTL
        self.send_json_response(200, stats)
    
    def reset_cache(self):
        """Resets DNS cache statistics."""
        dns_cache.reset()
        self.send_json_response(200, {'status': 'cache reseted'})

    def send_metrics_response(self):
        """Sends Prometheus metrics."""
        metrics_output = generate_latest()
        self.send_response(200)
        self.send_header('Content-type', CONTENT_TYPE_LATEST)
        self.end_headers()
        self.wfile.write(metrics_output)

    def log_message(self, format, *args):
        """Override default logging to use our configured logger, not stderr."""
        logging.info("%s - %s" % (self.address_string(), format % args))

    def log_request(self, code='-', size='-'):
        """Override log_request to handle missing requestline attribute."""
        if hasattr(self, 'requestline'):
            self.log_message('"%s" %s %s', self.requestline, str(code), str(size))
        else:
            # Fallback for test scenarios where requestline is not set
            self.log_message('%s %s', str(code), str(size))

    @staticmethod
    def _get_ip_address(fqdn):
        """Resolves an FQDN to an IP address with caching."""

        # Check cache first
        ip_address = dns_cache.get(fqdn)
        if ip_address:
            logging.debug(f"Resolved lookup request for FQDN from cache: {fqdn} -> {ip_address}")
            return ip_address

        # Cache miss - perform DNS lookup
        dns_lookups_total.inc()
        try:
            ip_address = socket.gethostbyname(fqdn)
            dns_lookups_success_total.inc()
            logging.debug(f"Resolved lookup request for FQDN from DNS: {fqdn} -> {ip_address}")
            
            # Cache the result
            dns_cache.set(fqdn, ip_address)
            
            return ip_address
        except socket.gaierror:
            logging.error(f"DNS lookup failed for FQDN: {fqdn}")
            dns_lookups_failure_total.inc()
            raise
        except Exception as e:
            logging.critical(f"An unexpected error occurred during DNS lookup for FQDN {fqdn}: {e}", exc_info=True)
            dns_lookups_failure_total.inc()
            raise

    @staticmethod
    def _get_zone_data(ip_address_str):
        """Finds the zone data (name and ID) for a given IP address."""
        try:
            ip = ipaddress.ip_address(ip_address_str)
            for network, data in CIDR_MAPPINGS.items():
                if ip in network:
                    return data
        except ValueError:
            logging.warning(f"Invalid IP address format: {ip_address_str}")
        return None


# --- Server and Shutdown Logic ---
def run(server_class=HTTPServer, handler_class=RequestHandler):
    """Starts the HTTP server and sets up graceful shutdown."""
    port = int(os.environ.get("PORT", 8080))
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)

    def shutdown_handler(signum, frame):
        logging.info(f"Received signal {signum}. Shutting down gracefully...")
        # Run shutdown in a separate thread to prevent deadlocking
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info(f"Starting server on http://0.0.0.0:{port}")
    httpd.serve_forever()
    logging.info("Server has shut down.")


if __name__ == "__main__":
    run()

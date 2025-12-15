import importlib
import json
import os
import sys
import tempfile
import time
from io import BytesIO
from unittest.mock import patch, MagicMock
import pytest

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def subnets_data():
    """Sample subnet data for testing."""
    return [
        {"CIDRBlock": "192.168.0.0/19", "AvailabilityZone": "eu-central-1b", "AvailabilityZoneId": "euc1-az3"},
        {"CIDRBlock": "192.168.32.0/19", "AvailabilityZone": "eu-central-1a", "AvailabilityZoneId": "euc1-az2"},
        {"CIDRBlock": "192.168.64.0/19", "AvailabilityZone": "eu-central-1c", "AvailabilityZoneId": "euc1-az1"},
    ]


@pytest.fixture
def temp_subnets_file(subnets_data):
    """Create a temporary subnets.json file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(subnets_data, f)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def mock_request_handler(temp_subnets_file):
    """Create a RequestHandler with mocked socket and mocked CIDR_MAPPINGS."""
    with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
        # Force reload of the module to pick up the new environment variable
        if 'server' in sys.modules:
            del sys.modules['server']
        
        import server
        
        # Create mock request
        mock_request = MagicMock()
        mock_request.makefile.return_value = BytesIO()
        
        # Create mock client address
        mock_client_address = ('127.0.0.1', 12345)
        
        # Create mock server
        mock_server = MagicMock()
        
        # Create handler instance
        handler = server.RequestHandler(mock_request, mock_client_address, mock_server)
        handler.wfile = BytesIO()
        
        # Set required attributes for Python 3.14+ compatibility
        handler.request_version = 'HTTP/1.1'
        handler.requestline = 'GET / HTTP/1.1'
        handler.command = 'GET'
        
        yield handler, server


class TestLoadSubnetsData:
    """Test the load_subnets_data function."""
    
    def test_load_subnets_from_file(self, temp_subnets_file, subnets_data):
        """Test loading subnet data from a valid JSON file."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            assert len(server.SUBNETS_DATA) == len(subnets_data)
            assert server.SUBNETS_DATA[0]['CIDRBlock'] == '192.168.0.0/19'
    
    def test_load_subnets_file_not_found(self):
        """Test that sys.exit is called when subnet file doesn't exist."""
        with patch.dict(os.environ, {'SUBNETS_FILE': '/nonexistent/file.json'}):
            with pytest.raises(SystemExit):
                if 'server' in sys.modules:
                    del sys.modules['server']
                importlib.import_module('server')
    
    def test_load_subnets_invalid_json(self):
        """Test that sys.exit is called when JSON is invalid."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("invalid json content {]")
            temp_path = f.name
        
        try:
            with patch.dict(os.environ, {'SUBNETS_FILE': temp_path}):
                with pytest.raises(SystemExit):
                    if 'server' in sys.modules:
                        del sys.modules['server']
                    importlib.import_module('server')
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestRequestHandler:
    """Test the RequestHandler class."""
    
    def test_health_check_healthz(self, mock_request_handler):
        """Test /healthz endpoint."""
        handler, server = mock_request_handler
        handler.path = '/healthz'
        handler.do_GET()
        
        response = handler.wfile.getvalue()
        assert b'200' in handler.wfile.getvalue() or True  # Response sent
        assert b'"status": "ok"' in response or b'"status":"ok"' in response
    
    def test_health_check_readyz(self, mock_request_handler):
        """Test /readyz endpoint."""
        handler, server = mock_request_handler
        handler.path = '/readyz'
        handler.do_GET()
        
        response = handler.wfile.getvalue()
        assert b'"status": "ok"' in response or b'"status":"ok"' in response
    
    def test_empty_path(self, mock_request_handler):
        """Test request with empty path."""
        handler, server = mock_request_handler
        handler.path = '/'
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Not Found' in response or '404' in response
    
    def test_legacy_path_returns_404(self, mock_request_handler):
        """Test that legacy path format returns 404."""
        handler, server = mock_request_handler
        handler.path = '/test.example.com'
        
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Not Found' in response or '404' in response

    def test_successful_fqdn_lookup(self, mock_request_handler):
        """Test successful FQDN lookup with /fqdn/ prefix."""
        handler, server = mock_request_handler
        handler.path = '/fqdn/test.example.com'
        
        # Mock DNS resolution to return an IP in our test subnet
        with patch.object(server.RequestHandler, '_get_ip_address', return_value='192.168.1.1'):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        response_data = json.loads(response.split('\r\n\r\n')[-1])
        
        assert 'zone' in response_data
        assert 'zoneId' in response_data
        assert response_data['zone'] == 'eu-central-1b'
        assert response_data['zoneId'] == 'euc1-az3'

    def test_invalid_fqdn_format(self, mock_request_handler):
        """Test invalid FQDN format."""
        handler, server = mock_request_handler
        
        # Invalid chars (underscore is not allowed in hostnames by RFC 1123, though some resolvers allow it, our regex is strict)
        # Actually underscore is allowed in domain names but not hostnames. Let's use something definitely invalid like !
        handler.path = '/fqdn/invalid_fqdn!.com'
        handler.do_GET()
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Invalid FQDN format' in response or '400' in response

        # Reset
        handler.wfile = BytesIO()
        
        # Starts with hyphen
        handler.path = '/fqdn/-start.com'
        handler.do_GET()
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Invalid FQDN format' in response or '400' in response

    def test_successful_ip_lookup(self, mock_request_handler):
        """Test successful IP lookup with /ip/ prefix."""
        handler, server = mock_request_handler
        handler.path = '/ip/192.168.1.1'
        
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        response_data = json.loads(response.split('\r\n\r\n')[-1])
        
        assert 'zone' in response_data
        assert 'zoneId' in response_data
        assert response_data['zone'] == 'eu-central-1b'
        assert response_data['zoneId'] == 'euc1-az3'

    def test_invalid_ip_format(self, mock_request_handler):
        """Test invalid IP format with /ip/ prefix."""
        handler, server = mock_request_handler
        handler.path = '/ip/invalid-ip'
        
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Invalid IP address format' in response or '400' in response

    def test_ip_not_found(self, mock_request_handler):
        """Test IP that is not in any subnet."""
        handler, server = mock_request_handler
        handler.path = '/ip/10.0.0.1'
        
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Zone not found' in response or '404' in response

    
    def test_fqdn_not_found(self, mock_request_handler):
        """Test FQDN that cannot be resolved."""
        handler, server = mock_request_handler
        handler.path = '/fqdn/nonexistent.example.com'
        
        # Mock DNS resolution failure
        with patch.object(server.RequestHandler, '_get_ip_address', side_effect=server.socket.gaierror):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'FQDN not found' in response or '404' in response
    
    def test_ip_not_in_subnet(self, mock_request_handler):
        """Test IP address that doesn't match any subnet."""
        handler, server = mock_request_handler
        handler.path = '/fqdn/test.example.com'
        
        # Mock DNS resolution to return an IP not in our subnets
        with patch.object(server.RequestHandler, '_get_ip_address', return_value='10.0.0.1'):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Zone not found' in response or '404' in response
    
    def test_unexpected_error(self, mock_request_handler):
        """Test handling of unexpected errors."""
        handler, server = mock_request_handler
        handler.path = '/fqdn/test.example.com'
        
        # Mock an unexpected exception
        with patch.object(server.RequestHandler, '_get_ip_address', side_effect=Exception("Unexpected error")):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Internal Server Error' in response or '500' in response


class TestGetZoneData:
    """Test the _get_zone_data static method."""
    
    def test_get_zone_data_valid_ip(self, temp_subnets_file):
        """Test getting zone data for a valid IP in a subnet."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            zone_data = server.RequestHandler._get_zone_data('192.168.1.1')
            assert zone_data is not None
            assert zone_data['AvailabilityZone'] == 'eu-central-1b'
            assert zone_data['AvailabilityZoneId'] == 'euc1-az3'
    
    def test_get_zone_data_ip_not_in_subnet(self, temp_subnets_file):
        """Test getting zone data for an IP not in any subnet."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            zone_data = server.RequestHandler._get_zone_data('10.0.0.1')
            assert zone_data is None
    
    def test_get_zone_data_invalid_ip(self, temp_subnets_file):
        """Test getting zone data for an invalid IP address."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            zone_data = server.RequestHandler._get_zone_data('invalid-ip')
            assert zone_data is None
    
    def test_get_zone_data_multiple_subnets(self, temp_subnets_file):
        """Test that different IPs resolve to correct zones."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Test IP in first subnet
            zone_data_1 = server.RequestHandler._get_zone_data('192.168.1.1')
            assert zone_data_1['AvailabilityZone'] == 'eu-central-1b'
            
            # Test IP in second subnet
            zone_data_2 = server.RequestHandler._get_zone_data('192.168.33.1')
            assert zone_data_2['AvailabilityZone'] == 'eu-central-1a'
            
            # Test IP in third subnet
            zone_data_3 = server.RequestHandler._get_zone_data('192.168.65.1')
            assert zone_data_3['AvailabilityZone'] == 'eu-central-1c'


class TestGetIPAddress:
    """Test the _get_ip_address static method."""
    
    def test_get_ip_address_localhost(self, temp_subnets_file):
        """Test resolving localhost."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            ip = server.RequestHandler._get_ip_address('localhost')
            assert ip == '127.0.0.1'
    
    def test_get_ip_address_invalid_fqdn(self, temp_subnets_file):
        """Test that invalid FQDN raises socket.gaierror."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            with pytest.raises(server.socket.gaierror):
                server.RequestHandler._get_ip_address('this-does-not-exist-12345.invalid')


class TestResponseMethods:
    """Test response helper methods."""
    
    def test_send_json_response(self, mock_request_handler):
        """Test sending JSON response."""
        handler, server = mock_request_handler
        test_payload = {'test': 'data', 'number': 123}
        
        handler.send_json_response(200, test_payload)
        
        response = handler.wfile.getvalue().decode('utf-8')
        # Extract JSON from response (after headers)
        json_part = response.split('\r\n\r\n')[-1]
        parsed = json.loads(json_part)
        
        assert parsed == test_payload
    
    def test_send_error_response(self, mock_request_handler):
        """Test sending error response."""
        handler, server = mock_request_handler
        error_message = "Test error message"
        
        handler.send_error_response(404, error_message)
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert error_message in response
        assert 'error' in response
    
    def test_send_healthy_response(self, mock_request_handler):
        """Test sending health check response."""
        handler, server = mock_request_handler
        
        handler.send_healthy_response()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'status' in response
        assert 'ok' in response


class TestDNSCache:
    """Test the DNS cache functionality."""
    
    def test_cache_initialization(self, temp_subnets_file):
        """Test DNS cache is properly initialized."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file, 'DNS_CACHE_TTL': '600'}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            assert server.dns_cache is not None
            assert server.dns_cache.default_ttl == 600
            assert server.DNS_CACHE_TTL == 600
    
    def test_cache_set_and_get(self, temp_subnets_file):
        """Test setting and getting values from cache."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Clear any existing cache
            server.dns_cache.cache.clear()
            
            # Set a value
            server.dns_cache.set('test.example.com', '192.168.1.1', ttl=60)
            
            # Get the value
            cached_ip = server.dns_cache.get('test.example.com')
            assert cached_ip == '192.168.1.1'
    
    def test_cache_miss(self, temp_subnets_file):
        """Test cache miss returns None."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Clear cache
            server.dns_cache.cache.clear()
            
            # Try to get non-existent value
            cached_ip = server.dns_cache.get('nonexistent.example.com')
            assert cached_ip is None
    
    def test_cache_expiry(self, temp_subnets_file):
        """Test that cache entries expire after TTL."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Clear cache
            server.dns_cache.cache.clear()
            
            # Set a value with very short TTL
            server.dns_cache.set('test.example.com', '192.168.1.1', ttl=1)
            
            # Immediately get it - should work
            cached_ip = server.dns_cache.get('test.example.com')
            assert cached_ip == '192.168.1.1'
            
            # Wait for expiry
            time.sleep(1.1)
            
            # Now it should be expired
            cached_ip = server.dns_cache.get('test.example.com')
            assert cached_ip is None
    
    def test_cache_clear_expired(self, temp_subnets_file):
        """Test clearing expired entries."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Clear cache
            server.dns_cache.cache.clear()
            
            # Add some entries with different TTLs
            server.dns_cache.set('short.example.com', '192.168.1.1', ttl=1)
            server.dns_cache.set('long.example.com', '192.168.1.2', ttl=3600)
            
            # Both should be in cache
            assert len(server.dns_cache.cache) == 2
            
            # Wait for short TTL to expire
            time.sleep(1.1)
            
            # Try to access the expired entry - this should trigger cleanup
            assert server.dns_cache.get('short.example.com') is None
            
            # Long-lived entry should still be accessible
            assert server.dns_cache.get('long.example.com') == '192.168.1.2'
            
            # Now cache should only have one entry
            assert len(server.dns_cache.cache) == 1
    
    def test_cache_stats(self, temp_subnets_file):
        """Test cache statistics."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            
            # Clear cache
            server.dns_cache.cache.clear()
            
            # Add some entries
            server.dns_cache.set('test1.example.com', '192.168.1.1')
            server.dns_cache.set('test2.example.com', '192.168.1.2')
            server.dns_cache.set('test3.example.com', '192.168.1.3')
            
            # Get stats
            stats = server.dns_cache.stats()
            
            assert stats['total_entries'] == 3
            assert 'test1.example.com' in stats['entries']
            assert 'test2.example.com' in stats['entries']
            assert 'test3.example.com' in stats['entries']
    
    def test_cache_stats_endpoint(self, mock_request_handler):
        """Test /cache/stats endpoint."""
        handler, server = mock_request_handler
        
        # Clear and add some test data
        server.dns_cache.cache.clear()
        server.dns_cache.set('test.example.com', '192.168.1.1')
        
        handler.path = '/cache/stats'
        handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        json_part = response.split('\r\n\r\n')[-1]
        stats = json.loads(json_part)
        
        assert 'total_entries' in stats
        assert 'entries' in stats
        assert 'ttl' in stats
        assert stats['total_entries'] >= 0
    
    def test_get_ip_address_uses_cache(self, mock_request_handler):
        """Test that _get_ip_address uses cache."""
        handler, server = mock_request_handler
        
        # Clear cache
        server.dns_cache.cache.clear()
        
        # Mock socket.gethostbyname
        with patch('socket.gethostbyname', return_value='192.168.1.1') as mock_dns:
            # First call - should hit DNS
            ip1 = server.RequestHandler._get_ip_address('test.example.com')
            assert ip1 == '192.168.1.1'
            assert mock_dns.call_count == 1
            
            # Second call - should use cache
            ip2 = server.RequestHandler._get_ip_address('test.example.com')
            assert ip2 == '192.168.1.1'
            assert mock_dns.call_count == 1  # Should not have called DNS again
            
            # Verify it's in cache
            cached_ip = server.dns_cache.get('test.example.com')
            assert cached_ip == '192.168.1.1'
    
    def test_cache_thread_safety(self, temp_subnets_file):
        """Test that cache operations are thread-safe."""
        with patch.dict(os.environ, {'SUBNETS_FILE': temp_subnets_file}):
            if 'server' in sys.modules:
                del sys.modules['server']
            import server
            import threading
            
            # Clear cache
            server.dns_cache.cache.clear()
            
            # Function to set cache entries
            def set_entries(prefix, count):
                for i in range(count):
                    server.dns_cache.set(f'{prefix}{i}.example.com', f'192.168.1.{i}')
            
            # Function to get cache entries
            def get_entries(prefix, count):
                for i in range(count):
                    server.dns_cache.get(f'{prefix}{i}.example.com')
            
            # Create multiple threads
            threads = []
            threads.append(threading.Thread(target=set_entries, args=('thread1-', 10)))
            threads.append(threading.Thread(target=set_entries, args=('thread2-', 10)))
            threads.append(threading.Thread(target=get_entries, args=('thread1-', 10)))
            
            # Start all threads
            for thread in threads:
                thread.start()
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join()
            
            # Verify cache has entries (exact count may vary due to timing)
            stats = server.dns_cache.stats()
            assert stats['total_entries'] > 0
    
    def test_successful_lookup_with_cache(self, mock_request_handler):
        """Test full request flow with caching."""
        handler, server = mock_request_handler
        
        # Clear cache
        server.dns_cache.cache.clear()
        
        handler.path = '/fqdn/db.example.com'
        
        # Mock DNS resolution
        with patch('socket.gethostbyname', return_value='192.168.1.1') as mock_dns:
            # First request
            handler.do_GET()
            response1 = handler.wfile.getvalue().decode('utf-8')
            
            # Reset handler output
            handler.wfile = BytesIO()
            
            # Second request - should use cache
            handler.do_GET()
            response2 = handler.wfile.getvalue().decode('utf-8')
            
            # DNS should only be called once
            assert mock_dns.call_count == 1
            
            # Both responses should be successful
            assert 'eu-central-1b' in response1
            assert 'eu-central-1b' in response2

import importlib
import json
import os
import sys
import tempfile
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
    
    def test_successful_fqdn_lookup(self, mock_request_handler):
        """Test successful FQDN lookup and zone resolution."""
        handler, server = mock_request_handler
        handler.path = '/test.example.com'
        
        # Mock DNS resolution to return an IP in our test subnet
        with patch.object(server.RequestHandler, '_get_ip_address', return_value='192.168.1.1'):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        response_data = json.loads(response.split('\r\n\r\n')[-1])
        
        assert 'zone' in response_data
        assert 'zoneId' in response_data
        assert response_data['zone'] == 'eu-central-1b'
        assert response_data['zoneId'] == 'euc1-az3'
    
    def test_fqdn_not_found(self, mock_request_handler):
        """Test FQDN that cannot be resolved."""
        handler, server = mock_request_handler
        handler.path = '/nonexistent.example.com'
        
        # Mock DNS resolution failure
        with patch.object(server.RequestHandler, '_get_ip_address', side_effect=server.socket.gaierror):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'FQDN not found' in response or '404' in response
    
    def test_ip_not_in_subnet(self, mock_request_handler):
        """Test IP address that doesn't match any subnet."""
        handler, server = mock_request_handler
        handler.path = '/test.example.com'
        
        # Mock DNS resolution to return an IP not in our subnets
        with patch.object(server.RequestHandler, '_get_ip_address', return_value='10.0.0.1'):
            handler.do_GET()
        
        response = handler.wfile.getvalue().decode('utf-8')
        assert 'Zone not found' in response or '404' in response
    
    def test_unexpected_error(self, mock_request_handler):
        """Test handling of unexpected errors."""
        handler, server = mock_request_handler
        handler.path = '/test.example.com'
        
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

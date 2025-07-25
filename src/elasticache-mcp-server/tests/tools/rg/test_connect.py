# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for replication group connection tools."""

import pytest
from awslabs.elasticache_mcp_server.tools.rg.connect import (
    _configure_security_groups,
    connect_jump_host_rg,
    create_jump_host_rg,
    get_ssh_tunnel_command_rg,
)
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_configure_security_groups_success():
    """Test successful security group configuration."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock ElastiCache responses
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [
            {
                'MemberClusters': ['cluster-1', 'cluster-2'],
            }
        ]
    }
    first_cluster_response = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
                'SecurityGroups': [{'SecurityGroupId': 'sg-123'}],
                'CacheNodes': [{'Endpoint': {'Port': 6379}}],
            }
        ]
    }
    second_cluster_response = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
                'SecurityGroups': [{'SecurityGroupId': 'sg-123'}],
                'CacheNodes': [{'Endpoint': {'Port': 6379}}],
            }
        ]
    }
    # Need to provide enough responses for all calls:
    # 1 call to get first cluster details
    # 2 final calls in the loop for security groups (cluster-1, cluster-2)
    mock_elasticache.describe_cache_clusters.side_effect = [
        first_cluster_response,  # First call to get first cluster details
        first_cluster_response,  # Second call in final loop (cluster-1)
        second_cluster_response,  # Third call in final loop (cluster-2)
    ]
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    # Mock EC2 responses
    mock_ec2.describe_instances.return_value = {
        'Reservations': [
            {
                'Instances': [
                    {
                        'VpcId': 'vpc-123',
                        'SecurityGroups': [{'GroupId': 'sg-456'}],
                    }
                ]
            }
        ]
    }
    mock_ec2.describe_security_groups.return_value = {'SecurityGroups': [{'IpPermissions': []}]}

    # Call function
    success, vpc_id, port = await _configure_security_groups(
        'rg-test',
        'i-123',
        ec2_client=mock_ec2,
        elasticache_client=mock_elasticache,
    )

    # Verify responses
    assert success is True
    assert vpc_id == 'vpc-123'
    assert port == 6379

    # Verify security group rule was added
    mock_ec2.authorize_security_group_ingress.assert_called_once()


@pytest.mark.asyncio
async def test_configure_security_groups_vpc_mismatch():
    """Test security group configuration with VPC mismatch."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock responses that create VPC mismatch
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'VpcId': 'vpc-456'}]}]
    }

    # Verify exception is raised
    with pytest.raises(ValueError) as excinfo:
        await _configure_security_groups(
            'rg-test',
            'i-123',
            ec2_client=mock_ec2,
            elasticache_client=mock_elasticache,
        )
    assert 'VPC' in str(excinfo.value)


@pytest.mark.asyncio
async def test_connect_jump_host_rg_success():
    """Test successful jump host connection."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock successful configuration
    with (
        patch(
            'awslabs.elasticache_mcp_server.tools.rg.connect._configure_security_groups',
            return_value=(True, 'vpc-123', 6379),
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
    ):
        result = await connect_jump_host_rg('rg-test', 'i-123')

    # Verify response
    assert result['Status'] == 'Success'
    assert result['InstanceId'] == 'i-123'
    assert result['ReplicationGroupId'] == 'rg-test'
    assert result['CachePort'] == 6379
    assert result['VpcId'] == 'vpc-123'
    assert result['SecurityGroupsConfigured'] is True


@pytest.mark.asyncio
async def test_get_ssh_tunnel_command_rg_success():
    """Test successful SSH tunnel command generation."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock EC2 responses
    mock_ec2.describe_instances.return_value = {
        'Reservations': [
            {
                'Instances': [
                    {
                        'KeyName': 'test-key',
                        'PublicDnsName': 'ec2-1-2-3-4.compute-1.amazonaws.com',
                        'Platform': '',
                    }
                ]
            }
        ]
    }

    # Mock ElastiCache responses with ConfigurationEndpoint
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [
            {
                'ConfigurationEndpoint': {'Address': 'config.cache.amazonaws.com', 'Port': 6379},
                'MemberClusters': ['cluster-1', 'cluster-2'],
            }
        ]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
    ):
        result = await get_ssh_tunnel_command_rg('rg-test', 'i-123')

    # Verify response
    assert result['keyName'] == 'test-key'
    assert result['user'] == 'ec2-user'
    assert result['jumpHostDns'] == 'ec2-1-2-3-4.compute-1.amazonaws.com'
    assert result['localPort'] == 6379
    assert result['remoteEndpoint'] == 'config.cache.amazonaws.com'
    assert result['remotePort'] == 6379

    # Verify command format
    assert 'command' in result
    assert 'ssh -i "test-key.pem"' in result['command']
    assert 'config.cache.amazonaws.com' in result['command']
    assert '6379' in result['command']


@pytest.mark.asyncio
async def test_create_jump_host_rg_success():
    """Test successful jump host creation."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock EC2 responses
    mock_ec2.describe_key_pairs.return_value = {'KeyPairs': [{'KeyName': 'test-key'}]}
    mock_ec2.describe_subnets.return_value = {'Subnets': [{'VpcId': 'vpc-123'}]}
    mock_ec2.describe_route_tables.return_value = {
        'RouteTables': [{'Routes': [{'GatewayId': 'igw-123'}]}]
    }
    mock_ec2.describe_images.return_value = {
        'Images': [{'ImageId': 'ami-123', 'CreationDate': '2023-01-01'}]
    }
    mock_ec2.describe_security_groups.return_value = {'SecurityGroups': [{'IpPermissions': []}]}
    mock_ec2.run_instances.return_value = {'Instances': [{'InstanceId': 'i-new'}]}
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'PublicIpAddress': '1.2.3.4'}]}]
    }

    # Mock ElastiCache responses
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
        patch(
            'awslabs.elasticache_mcp_server.tools.rg.connect._configure_security_groups',
            return_value=(True, 'vpc-123', 6379),
        ),
    ):
        result = await create_jump_host_rg(
            'rg-test', 'test-key', 'subnet-123', 'sg-123', 't3.small'
        )

    # Verify response
    assert result['InstanceId'] == 'i-new'
    assert result['PublicIpAddress'] == '1.2.3.4'
    assert result['InstanceType'] == 't3.small'
    assert result['SubnetId'] == 'subnet-123'
    assert result['SecurityGroupId'] == 'sg-123'
    assert result['ReplicationGroupId'] == 'rg-test'
    assert result['SecurityGroupsConfigured'] is True
    assert result['CachePort'] == 6379
    assert result['VpcId'] == 'vpc-123'

    # Verify instance creation
    mock_ec2.run_instances.assert_called_once()
    call_args = mock_ec2.run_instances.call_args[1]
    assert call_args['InstanceType'] == 't3.small'
    assert call_args['KeyName'] == 'test-key'
    assert call_args['NetworkInterfaces'][0]['SubnetId'] == 'subnet-123'
    assert call_args['NetworkInterfaces'][0]['Groups'] == ['sg-123']


@pytest.mark.asyncio
async def test_create_jump_host_rg_private_subnet():
    """Test jump host creation with private subnet."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Set up EC2 client with proper exception class and methods
    class MockEC2Client:
        def describe_key_pairs(self):
            pass

        def describe_subnets(self):
            pass

        def describe_route_tables(self):
            pass

        def describe_vpcs(self):
            pass

        def describe_instances(self):
            pass

        def describe_security_groups(self):
            pass

        def authorize_security_group_ingress(self):
            pass

        def run_instances(self):
            pass

        def describe_images(self):
            pass

        class Exceptions:
            class ClientError(Exception):
                pass

        exceptions = Exceptions()

    # Create mock with all required methods
    mock_ec2 = MagicMock(spec=MockEC2Client)
    mock_ec2.exceptions = MockEC2Client.exceptions

    # Set up mocks for EC2
    mock_ec2.describe_key_pairs.return_value = {'KeyPairs': [{'KeyName': 'test-key'}]}
    mock_ec2.describe_subnets.return_value = {
        'Subnets': [{'VpcId': 'vpc-123', 'DefaultForAz': False, 'MapPublicIpOnLaunch': False}]
    }
    mock_ec2.describe_route_tables.return_value = {'RouteTables': [{'Routes': []}]}
    mock_ec2.describe_vpcs.return_value = {'Vpcs': [{'IsDefault': False}]}
    mock_ec2.describe_images.return_value = {
        'Images': [{'ImageId': 'ami-123', 'CreationDate': '2023-01-01'}]
    }

    # Set up mocks for ElastiCache
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
    ):
        result = await create_jump_host_rg('rg-test', 'test-key', 'subnet-123', 'sg-123')
        assert 'error' in result
        assert (
            'Subnet subnet-123 is not public (no route to internet gateway found and not a default subnet in default VPC). The subnet must be public to allow SSH access to the jump host.'
            in result['error']
        )


@pytest.mark.asyncio
async def test_create_jump_host_rg_invalid_key():
    """Test jump host creation with invalid key pair."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Set up EC2 client with proper exception class and methods
    class MockEC2Client:
        def describe_key_pairs(self):
            pass

        def describe_subnets(self):
            pass

        def describe_route_tables(self):
            pass

        def describe_instances(self):
            pass

        def describe_security_groups(self):
            pass

        def authorize_security_group_ingress(self):
            pass

        def run_instances(self):
            pass

        def describe_images(self):
            pass

        class Exceptions:
            class ClientError(Exception):
                pass

        exceptions = Exceptions()

    # Create mock with all required methods
    mock_ec2 = MagicMock(spec=MockEC2Client)
    mock_ec2.exceptions = MockEC2Client.exceptions

    # Set up mock for key pair not found error
    mock_ec2.describe_key_pairs.side_effect = ClientError(
        {'Error': {'Code': 'InvalidKeyPair.NotFound', 'Message': 'Key not found'}},
        'DescribeKeyPairs',
    )
    mock_ec2.describe_images.return_value = {
        'Images': [{'ImageId': 'ami-123', 'CreationDate': '2023-01-01'}]
    }

    # Set up mocks for ElastiCache
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
    ):
        result = await create_jump_host_rg('rg-test', 'invalid-key', 'subnet-123', 'sg-123')
        assert 'error' in result
        assert "Key pair 'invalid-key' not found" in result['error']


@pytest.mark.asyncio
async def test_create_jump_host_rg_default_vpc_default_subnet():
    """Test jump host creation with default subnet in default VPC."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock EC2 responses for default VPC scenario
    mock_ec2.describe_key_pairs.return_value = {'KeyPairs': [{'KeyName': 'test-key'}]}
    mock_ec2.describe_subnets.return_value = {
        'Subnets': [
            {
                'VpcId': 'vpc-123',
                'DefaultForAz': True,  # This is a default subnet
                'MapPublicIpOnLaunch': True,
            }
        ]
    }
    mock_ec2.describe_route_tables.return_value = {'RouteTables': [{'Routes': []}]}  # No IGW route
    mock_ec2.describe_vpcs.return_value = {'Vpcs': [{'IsDefault': True}]}  # Default VPC
    mock_ec2.describe_images.return_value = {
        'Images': [{'ImageId': 'ami-123', 'CreationDate': '2023-01-01'}]
    }
    mock_ec2.describe_security_groups.return_value = {'SecurityGroups': [{'IpPermissions': []}]}
    mock_ec2.run_instances.return_value = {'Instances': [{'InstanceId': 'i-new'}]}
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'PublicIpAddress': '1.2.3.4'}]}]
    }

    # Mock ElastiCache responses
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
        patch(
            'awslabs.elasticache_mcp_server.tools.rg.connect._configure_security_groups',
            return_value=(True, 'vpc-123', 6379),
        ),
    ):
        result = await create_jump_host_rg(
            'rg-test', 'test-key', 'subnet-123', 'sg-123', 't3.small'
        )

    # Verify successful creation despite no IGW route (because it's default subnet in default VPC)
    assert result['InstanceId'] == 'i-new'
    assert result['PublicIpAddress'] == '1.2.3.4'
    assert result['InstanceType'] == 't3.small'
    assert result['SubnetId'] == 'subnet-123'
    assert result['SecurityGroupId'] == 'sg-123'
    assert result['ReplicationGroupId'] == 'rg-test'
    assert result['SecurityGroupsConfigured'] is True
    assert result['CachePort'] == 6379
    assert result['VpcId'] == 'vpc-123'


@pytest.mark.asyncio
async def test_create_jump_host_rg_default_vpc_map_public_ip():
    """Test jump host creation with MapPublicIpOnLaunch=True in default VPC."""
    mock_ec2 = MagicMock()
    mock_elasticache = MagicMock()

    # Mock EC2 responses for default VPC scenario with MapPublicIpOnLaunch
    mock_ec2.describe_key_pairs.return_value = {'KeyPairs': [{'KeyName': 'test-key'}]}
    mock_ec2.describe_subnets.return_value = {
        'Subnets': [
            {
                'VpcId': 'vpc-123',
                'DefaultForAz': False,  # Not a default subnet
                'MapPublicIpOnLaunch': True,  # But has MapPublicIpOnLaunch=True
            }
        ]
    }
    mock_ec2.describe_route_tables.return_value = {'RouteTables': [{'Routes': []}]}  # No IGW route
    mock_ec2.describe_vpcs.return_value = {'Vpcs': [{'IsDefault': True}]}  # Default VPC
    mock_ec2.describe_images.return_value = {
        'Images': [{'ImageId': 'ami-123', 'CreationDate': '2023-01-01'}]
    }
    mock_ec2.describe_security_groups.return_value = {'SecurityGroups': [{'IpPermissions': []}]}
    mock_ec2.run_instances.return_value = {'Instances': [{'InstanceId': 'i-new'}]}
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'PublicIpAddress': '1.2.3.4'}]}]
    }

    # Mock ElastiCache responses
    mock_elasticache.describe_replication_groups.return_value = {
        'ReplicationGroups': [{'MemberClusters': ['cluster-1']}]
    }
    mock_elasticache.describe_cache_clusters.return_value = {
        'CacheClusters': [
            {
                'CacheSubnetGroupName': 'subnet-group-1',
            }
        ]
    }
    mock_elasticache.describe_cache_subnet_groups.return_value = {
        'CacheSubnetGroups': [{'VpcId': 'vpc-123'}]
    }

    with (
        patch(
            'awslabs.elasticache_mcp_server.common.connection.EC2ConnectionManager.get_connection',
            return_value=mock_ec2,
        ),
        patch(
            'awslabs.elasticache_mcp_server.common.connection.ElastiCacheConnectionManager.get_connection',
            return_value=mock_elasticache,
        ),
        patch(
            'awslabs.elasticache_mcp_server.tools.rg.connect._configure_security_groups',
            return_value=(True, 'vpc-123', 6379),
        ),
    ):
        result = await create_jump_host_rg(
            'rg-test', 'test-key', 'subnet-123', 'sg-123', 't3.small'
        )

    # Verify successful creation despite no IGW route (because MapPublicIpOnLaunch=True in default VPC)
    assert result['InstanceId'] == 'i-new'
    assert result['PublicIpAddress'] == '1.2.3.4'
    assert result['InstanceType'] == 't3.small'
    assert result['SubnetId'] == 'subnet-123'
    assert result['SecurityGroupId'] == 'sg-123'
    assert result['ReplicationGroupId'] == 'rg-test'
    assert result['SecurityGroupsConfigured'] is True
    assert result['CachePort'] == 6379
    assert result['VpcId'] == 'vpc-123'

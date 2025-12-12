#!/usr/bin/env python3
"""
Test script to validate the Kyverno policy JMESPath precondition logic.
This simulates the precondition check that prevents duplicate zone placement.
"""

import json
import jmespath

def test_jmespath_precondition():
    """Test the JMESPath expression used in the Kyverno policy precondition."""
    
    # The JMESPath expression from the policy
    jmespath_expr = "[?preference.matchExpressions[?key=='topology.kubernetes.io/zone']] | length(@)"
    
    test_cases = [
        {
            "name": "Pod without any affinity (nil case)",
            "value": None,
            "expected_length": 0,
            "should_pass": True
        },
        {
            "name": "Pod with empty preferredDuringScheduling array",
            "value": [],
            "expected_length": 0,
            "should_pass": True
        },
        {
            "name": "Pod with zone affinity already configured",
            "value": [
                {
                    "weight": 100,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": "topology.kubernetes.io/zone",
                                "operator": "In",
                                "values": ["eu-central-1a"]
                            }
                        ]
                    }
                }
            ],
            "expected_length": 1,
            "should_pass": False
        },
        {
            "name": "Pod with different affinity (not zone-based)",
            "value": [
                {
                    "weight": 50,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": "node.kubernetes.io/instance-type",
                                "operator": "In",
                                "values": ["t3.large"]
                            }
                        ]
                    }
                }
            ],
            "expected_length": 0,
            "should_pass": True
        },
        {
            "name": "Pod with multiple affinities including zone",
            "value": [
                {
                    "weight": 50,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": "node.kubernetes.io/instance-type",
                                "operator": "In",
                                "values": ["t3.large"]
                            }
                        ]
                    }
                },
                {
                    "weight": 100,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": "topology.kubernetes.io/zone",
                                "operator": "In",
                                "values": ["eu-central-1b"]
                            }
                        ]
                    }
                }
            ],
            "expected_length": 1,
            "should_pass": False
        }
    ]
    
    print("Testing JMESPath precondition logic")
    print("=" * 80)
    print(f"Expression: {jmespath_expr}")
    print("=" * 80)
    print()
    
    all_passed = True
    
    for test_case in test_cases:
        print(f"Test: {test_case['name']}")
        print(f"Input: {json.dumps(test_case['value'], indent=2)}")
        
        # Handle None case (simulating the || `[]` default in Kyverno)
        value = test_case['value'] if test_case['value'] is not None else []
        
        try:
            result = jmespath.search(jmespath_expr, value)
            print(f"Result: {result}")
            print(f"Expected: {test_case['expected_length']}")
            
            # Check if precondition would pass (length == 0)
            precondition_passes = (result == 0)
            print(f"Precondition passes (allows mutation): {precondition_passes}")
            print(f"Expected to pass: {test_case['should_pass']}")
            
            if precondition_passes == test_case['should_pass'] and result == test_case['expected_length']:
                print("✅ PASS")
            else:
                print("❌ FAIL")
                all_passed = False
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
            all_passed = False
        
        print()
        print("-" * 80)
        print()
    
    assert all_passed, "Some test cases failed"


if __name__ == "__main__":
    import sys
    try:
        test_jmespath_precondition()
        print("\n✅ All tests passed!")
        print("\nThe precondition correctly:")
        print("  - Allows mutation when no zone affinity exists")
        print("  - Blocks mutation when zone affinity is already configured")
        print("  - Handles nil/empty cases gracefully")
        sys.exit(0)
    except AssertionError:
        print("\n❌ Some tests failed!")
        sys.exit(1)

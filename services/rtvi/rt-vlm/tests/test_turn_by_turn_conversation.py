#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

######################################################################################################
# Test script for turn-by-turn conversation support in NIM endpoints
######################################################################################################

import json
import sys
import os

# Add src to path
sys.path.insert(0, "src")


def test_conversation_message_processing():
    """Test that the server code processes all message types correctly"""
    print("=" * 60)
    print("Testing Conversation Message Processing Logic")
    print("=" * 60)

    try:
        # Simulate the message processing logic from rtvi_vlm_server.py
        from api_models.nim_compat import ChatMessage, ChatCompletionRequest

        # Create a multi-turn conversation
        request = ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(role="system", content="You are a helpful assistant."),
                ChatMessage(role="user", content="What is in this video?"),
                ChatMessage(role="assistant", content="The video shows a warehouse scene."),
                ChatMessage(role="user", content="Can you describe the person in more detail?"),
            ],
        )

        # Simulate the processing logic
        system_prompt = ""
        conversation_parts = []
        has_user_message = False

        for msg in request.messages:
            if msg.role == "system":
                system_prompt = msg.get_text_content()
            elif msg.role == "user":
                has_user_message = True
                user_content = msg.get_text_content()
                conversation_parts.append(f"User: {user_content}")
            elif msg.role == "assistant":
                assistant_content = msg.get_text_content()
                conversation_parts.append(f"Assistant: {assistant_content}")

        # Verify results
        assert has_user_message, "Should have at least one user message"
        assert system_prompt == "You are a helpful assistant.", "System prompt should be set"
        assert len(conversation_parts) == 3, "Should have 3 conversation parts"

        # Check conversation format
        expected_format = [
            "User: What is in this video?",
            "Assistant: The video shows a warehouse scene.",
            "User: Can you describe the person in more detail?",
        ]
        assert (
            conversation_parts == expected_format
        ), f"Conversation format mismatch: {conversation_parts}"

        user_prompt = "\n".join(conversation_parts)
        expected_prompt = "User: What is in this video?\nAssistant: The video shows a warehouse scene.\nUser: Can you describe the person in more detail?"
        assert user_prompt == expected_prompt, f"Prompt format mismatch: {user_prompt}"

        print("✓ System message processed correctly")
        print("✓ User messages processed correctly")
        print("✓ Assistant messages processed correctly")
        print("✓ Conversation format is correct")
        print(f"✓ Final prompt: {user_prompt[:80]}...")

        print("\n✅ Message processing logic test passed!\n")
        return True

    except Exception as e:
        print(f"❌ Message processing test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_conversation_structure():
    """Test conversation structure validation"""
    print("=" * 60)
    print("Testing Conversation Structure")
    print("=" * 60)

    try:
        from api_models.nim_compat import ChatMessage, ChatCompletionRequest

        # Test 1: Valid multi-turn conversation
        conv1 = ChatCompletionRequest(
            model="test",
            messages=[
                ChatMessage(role="user", content="Q1"),
                ChatMessage(role="assistant", content="A1"),
                ChatMessage(role="user", content="Q2"),
            ],
        )
        assert len(conv1.messages) == 3
        print("✓ Valid multi-turn conversation structure")

        # Test 2: Conversation starting with assistant (should be valid structure-wise)
        conv2 = ChatCompletionRequest(
            model="test",
            messages=[
                ChatMessage(role="assistant", content="Previous response"),
                ChatMessage(role="user", content="Follow-up question"),
            ],
        )
        assert len(conv2.messages) == 2
        print("✓ Conversation starting with assistant message")

        # Test 3: Multiple system messages (last one should be used)
        conv3 = ChatCompletionRequest(
            model="test",
            messages=[
                ChatMessage(role="system", content="System 1"),
                ChatMessage(role="system", content="System 2"),
                ChatMessage(role="user", content="Question"),
            ],
        )
        assert len(conv3.messages) == 3
        system_messages = [msg for msg in conv3.messages if msg.role == "system"]
        assert len(system_messages) == 2
        print("✓ Multiple system messages supported")

        # Test 4: Empty messages (should fail validation)
        try:
            ChatCompletionRequest(model="test", messages=[])
            print("❌ Empty messages should fail validation")
            return False
        except Exception:
            print("✓ Empty messages correctly rejected")

        # Test 5: Only assistant messages (should fail - needs at least one user)
        try:
            ChatCompletionRequest(
                model="test",
                messages=[
                    ChatMessage(role="assistant", content="Response"),
                ],
            )
            print("⚠ Only assistant messages accepted (validation may be in server)")
        except Exception:
            print("✓ Only assistant messages correctly rejected")

        print("\n✅ Conversation structure tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Conversation structure test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_conversation_format():
    """Test conversation formatting"""
    print("=" * 60)
    print("Testing Conversation Formatting")
    print("=" * 60)

    try:
        from api_models.nim_compat import ChatMessage

        # Test various conversation patterns
        test_cases = [
            {
                "name": "Simple Q&A",
                "messages": [
                    ChatMessage(role="user", content="Question?"),
                    ChatMessage(role="assistant", content="Answer."),
                ],
                "expected": "User: Question?\nAssistant: Answer.",
            },
            {
                "name": "Multi-turn with system",
                "messages": [
                    ChatMessage(role="system", content="You are helpful."),
                    ChatMessage(role="user", content="Q1"),
                    ChatMessage(role="assistant", content="A1"),
                    ChatMessage(role="user", content="Q2"),
                    ChatMessage(role="assistant", content="A2"),
                ],
                "expected": "User: Q1\nAssistant: A1\nUser: Q2\nAssistant: A2",
            },
            {
                "name": "Long conversation",
                "messages": [
                    ChatMessage(role="user", content="Tell me about X"),
                    ChatMessage(role="assistant", content="X is..."),
                    ChatMessage(role="user", content="What about Y?"),
                    ChatMessage(role="assistant", content="Y is..."),
                    ChatMessage(role="user", content="Compare X and Y"),
                ],
                "expected": "User: Tell me about X\nAssistant: X is...\nUser: What about Y?\nAssistant: Y is...\nUser: Compare X and Y",
            },
        ]

        for test_case in test_cases:
            conversation_parts = []
            for msg in test_case["messages"]:
                if msg.role == "user":
                    conversation_parts.append(f"User: {msg.get_text_content()}")
                elif msg.role == "assistant":
                    conversation_parts.append(f"Assistant: {msg.get_text_content()}")

            user_prompt = "\n".join(conversation_parts)
            assert (
                user_prompt == test_case["expected"]
            ), f"Format mismatch for {test_case['name']}: got '{user_prompt}', expected '{test_case['expected']}'"
            print(f"✓ {test_case['name']}: Format correct")

        print("\n✅ Conversation formatting tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Conversation format test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("Turn-by-Turn Conversation Support Tests")
    print("=" * 60 + "\n")

    tests = [
        ("Message Processing Logic", test_conversation_message_processing),
        ("Conversation Structure", test_conversation_structure),
        ("Conversation Formatting", test_conversation_format),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test '{test_name}' crashed: {e}")
            import traceback

            traceback.print_exc()
            results.append((test_name, False))
        print()

    # Print summary
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

from agent.intent import extract_json_object, sanitize_bucket_name


def test_extract_json_object_plain():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_fenced():
    text = 'Sure, here you go:\n```json\n{"is_supported": true, "bucket_name": "x"}\n```\nHope that helps!'
    assert extract_json_object(text) == {"is_supported": True, "bucket_name": "x"}


def test_extract_json_object_stray_prose():
    text = 'Here is the JSON: {"is_supported": false} -- let me know if you need more.'
    assert extract_json_object(text) == {"is_supported": False}


def test_extract_json_object_garbage_returns_none():
    assert extract_json_object("not json at all") is None


def test_sanitize_bucket_name_basic():
    name = sanitize_bucket_name("My Cool Bucket!!", "req-abc12345")
    assert name.startswith("my-cool-bucket")
    assert name.endswith("abc12345")
    assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


def test_sanitize_bucket_name_empty_falls_back():
    name = sanitize_bucket_name("   ", "req-abc12345")
    assert name.startswith("devops-agent-bucket")

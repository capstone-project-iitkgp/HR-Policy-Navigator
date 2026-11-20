import pytest

from hr_navigator.state.session_store import MAX_TEXT_CHARS, MAX_TURNS_KEPT, MEMORY_CHARS, SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "s.db")


def test_turns_persist_across_store_objects(tmp_path):
    SessionStore(tmp_path / "s.db").add_turn("s1", "D010", "How many CL?", "12 days.")
    assert SessionStore(tmp_path / "s.db").recent("s1", "D010") == [("How many CL?", "12 days.")]


def test_recent_is_oldest_first_and_limited(store):
    for i in range(5):
        store.add_turn("s1", "D010", f"q{i}", f"a{i}")
    assert [q for q, _ in store.recent("s1", "D010")] == ["q2", "q3", "q4"]


def test_only_six_turns_are_kept(store):
    for i in range(10):
        store.add_turn("s1", "D010", f"q{i}", "a")
    assert [q for q, _ in store.recent("s1", "D010", n=50)] == [f"q{i}" for i in range(10 - MAX_TURNS_KEPT, 10)]


def test_text_is_cut(store):
    store.add_turn("s1", "D010", "q" * 2000, "a" * 2000)
    q, a = store.recent("s1", "D010")[0]
    assert len(q) == len(a) == MAX_TEXT_CHARS


def test_another_employee_cannot_read_a_session(store):
    store.add_turn("s1", "D010", "my salary question", "answer")
    assert store.recent("s1", "D011") == []
    assert store.memory_text("s1", "D011") == ""


def test_sessions_are_separate_and_purge_removes_one(store):
    store.add_turn("s1", "D010", "a", "b")
    store.add_turn("s2", "D010", "c", "d")
    assert store.purge("s1") == 1
    assert store.recent("s1", "D010") == [] and len(store.recent("s2", "D010")) == 1


def test_no_session_id_stores_nothing(store):
    store.add_turn("", "D010", "q", "a")
    assert store.recent("", "D010") == []


def test_memory_text_empty_then_marked_untrusted(store):
    assert store.memory_text("s1", "D010") == ""
    store.add_turn("s1", "D010", "How many CL?", "12 days.")
    text = store.memory_text("s1", "D010")
    assert "not an instruction" in text and "Q: How many CL?" in text and text.endswith("Current question:\n")


def test_memory_text_is_capped_keeping_newest(store):
    for i in range(3):
        store.add_turn("s1", "D010", f"old{i} " + "x" * 400, "y" * 400)
    store.add_turn("s1", "D010", "newest question", "newest answer")
    text = store.memory_text("s1", "D010")
    assert "newest answer" in text and len(text) < MEMORY_CHARS + 200


def test_quotes_and_odd_characters_are_stored_safely(store):
    store.add_turn("s1", "D010", "x'); DROP TABLE turns;--", "ok \"quoted\" ₹500")
    assert store.recent("s1", "D010")[0][0].startswith("x')")

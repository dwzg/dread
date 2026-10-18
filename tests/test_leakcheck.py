"""dread leakcheck: names, addresses and words of a private project are found in a tree and in its
history, and coincidences can be allowed."""
import subprocess

from dread import leakcheck


def setup_private(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "functions.tsv").write_text("address\tname\tbytes\n0x12340\tsecret_widget_init\t40\n"
                                           "0x12400\tmain\t8\n0x12480\tFUN_00012480\t4\n")
    (private / "symbols.txt").write_text("f 0x13000 blinker_task\nd 0x2fe000 g_hidden_state\n")
    (private / "words.txt").write_text("acme\n")
    return private


def test_finds_names_addresses_and_words(tmp_path, capsys):
    private = setup_private(tmp_path)
    tree = tmp_path / "public"
    tree.mkdir()
    (tree / "a.py").write_text("# calls secret_widget_init at 0x12340\nx = main\n")
    (tree / "b.md").write_text("Made for the ACME gadget; g_hidden_state lives at 0x2FE000.\n")
    (tree / "clean.txt").write_text("FUN_00012480 is a generic name, 0x10 is small\n")
    rc = leakcheck.main(["--tree", str(tree), "--tables", str(private / "functions.tsv"), str(private / "symbols.txt"),
                         "--words", str(private / "words.txt")])
    out = capsys.readouterr().out
    assert rc == 1
    for hit in ("a.py: secret_widget_init", "a.py: 0x12340", "b.md: acme", "b.md: g_hidden_state", "b.md: 0x2fe000"):
        assert "HIT " + hit in out
    assert "clean.txt" not in out and ": main" not in out


def test_allow_list_and_history(tmp_path, capsys):
    private = setup_private(tmp_path)
    tree = tmp_path / "public"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    (tree / "a.txt").write_text("blinker_task\n")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q",
                    "-m", "mentions 0x13000"], check=True)
    allow = tmp_path / "allow.txt"
    allow.write_text("a.txt:blinker_task\n")
    rc = leakcheck.main(["--tree", str(tree), "--tables", str(private / "symbols.txt"), "--allow", str(allow),
                         "--history"])
    out = capsys.readouterr().out
    assert "allowed a.txt: blinker_task" in out
    assert "HIT history" in out and "0x13000" in out and "blinker_task" in out    # the history is not allowed
    assert rc == 1

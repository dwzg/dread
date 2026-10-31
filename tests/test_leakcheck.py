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


def test_history_honours_path_allows(tmp_path, capsys):
    """A path:hit allow entry covers the lines of that file's diffs in the history too, and only
    those."""
    import subprocess
    from dread import leakcheck
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("entry 0x123456\n")
    (repo / "b.txt").write_text("also 0x123456\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "add"], check=True)
    table = tmp_path / "functions.tsv"
    table.write_text("0x123456\tprivate_function\t4\n")
    allow = tmp_path / "allow.txt"
    allow.write_text("a.txt:0x123456\nb.txt:0x123456\n")
    assert leakcheck.main(["--tree", str(repo), "--tables", str(table), "--allow", str(allow), "--history"]) == 0
    allow.write_text("a.txt:0x123456\n")
    assert leakcheck.main(["--tree", str(repo), "--tables", str(table), "--allow", str(allow), "--history"]) == 1
    out = capsys.readouterr().out
    assert "HIT b.txt: 0x123456" in out and "b.txt: 0x123456" in out.split("HIT history")[-1]


def test_the_hits_as_json_with_their_lines(tmp_path):
    """--json FILE: each hit in the tree with its file, the lines it is on and whether it is allowed; the
    history's with their commits; the commit authors; how many names, addresses and words were looked for;
    the private files' paths as given and nothing else of them (no name, address or word that did not match,
    no comment of theirs); written with mode 600."""
    import json
    import os
    import stat
    private = setup_private(tmp_path)
    (private / "words.txt").write_text("# canary-words-0193: a comment\nacme\n")
    allow = private / "allow.txt"
    allow.write_text("# canary-allow-0193\nb.md:0x2fe000\n")
    tree = tmp_path / "public"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    (tree / "a.py").write_text("x = 1\n# calls secret_widget_init\ny = 2\nsecret_widget_init()\n")
    (tree / "b.md").write_text("ACME made it; g_hidden_state lives at 0x2FE000.\n")
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "about blinker_task"], check=True)
    out = tmp_path / "out" / "check.json"
    tables = [str(private / "functions.tsv"), str(private / "symbols.txt")]
    rc = leakcheck.main(["--tree", str(tree), "--tables"] + tables + ["--words", str(private / "words.txt"),
                         "--allow", str(allow), "--history", "--json", str(out)])
    assert rc == 1 and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    data = json.loads(out.read_text())
    assert data["version"] == 1 and data["tree"] == str(tree) and data["tables"] == tables and data["history"] is True
    hits = {(h["file"], h["hit"]): h for h in data["hits"]}
    assert hits[("a.py", "secret_widget_init")]["lines"] == [2, 4] and not hits[("a.py", "secret_widget_init")]["allowed"]
    assert hits[("b.md", "acme")]["lines"] == [1] and hits[("b.md", "g_hidden_state")]["lines"] == [1]
    assert hits[("b.md", "0x2fe000")]["allowed"] is True and data["allowed"] == 1
    assert data["not_allowed"] == 3 + len(data["history_hits"]) and data["exit"] == 1
    assert {"commit", "file", "hit"} == set(data["history_hits"][0]) and any(
        h["hit"] == "blinker_task" and h["file"] is None for h in data["history_hits"])       # (the message)
    assert data["authors"] == ["t <t@example.org>"]
    assert data["looked_for"] == {"names": 3, "addresses": 5, "words": 1}
    text = out.read_text()
    for never in ("canary-words-0193", "canary-allow-0193", "0x12400", "0x12480", "FUN_00012480"):
        assert never not in text, never


def test_a_word_matched_across_lines_is_one_hit_its_allow_entry_one_line(tmp_path, capsys):
    """A word's expression that matches across a line break (a phrase a wrapped paragraph splits) is
    reported with its whitespace collapsed, and an allow entry of one line, its spaces collapsed too,
    passes it (before, the hit kept its newline and indentation, which no allow entry could match)."""
    private = setup_private(tmp_path)
    (private / "words.txt").write_text("widget[^.]{0,40}?ten units\n")
    tree = tmp_path / "public"
    tree.mkdir()
    (tree / "notes.md").write_text("The widget was measured at\n  ten units.\n")
    rc = leakcheck.main(["--tree", str(tree), "--tables", str(private / "symbols.txt"), "--words",
                         str(private / "words.txt")])
    out = capsys.readouterr().out
    assert rc == 1 and "HIT notes.md: widget was measured at ten units" in out, out
    allow = tmp_path / "allow.txt"
    allow.write_text("notes.md:widget was measured   at ten units\n")
    rc = leakcheck.main(["--tree", str(tree), "--tables", str(private / "symbols.txt"), "--words",
                         str(private / "words.txt"), "--allow", str(allow)])
    out = capsys.readouterr().out
    assert rc == 0 and "allowed notes.md: widget was measured at ten units" in out, out



def test_joined_words_only_filter_overlapping_hits_stay(tmp_path, capsys):
    """The joined expression only skips text no word matches: an allowed longer word must not hide a
    shorter one inside it that is not allowed."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("a line with LongerAllowedSecretName in it\nnothing here\n")
    tables = tmp_path / "functions.tsv"
    tables.write_text("address\tname\n")
    words = tmp_path / "words.txt"
    words.write_text("LongerAllowedSecretName\nSecretName\n")
    allow = tmp_path / "allow.txt"
    allow.write_text("longerallowedsecretname\n")
    from dread import leakcheck
    import re
    words_c = [re.compile(w, re.I) for w in ["LongerAllowedSecretName", "SecretName", r"a\.b+c", "short"]]
    select = leakcheck.prefilter(words_c)
    # only the words whose first characters stand in the text, plus every expression and short word
    assert [w.pattern for w in select("xx secretnamE yy")] == ["SecretName", r"a\.b+c", "short"]
    code = leakcheck.main(["--tree", str(tree), "--tables", str(tables), "--words", str(words), "--allow", str(allow)])
    out = capsys.readouterr().out
    assert code == 1 and "secretname" in out.lower()

"""dread.namejudge: proposed function names judged against the source they were compiled from. A
function's definition is cut from its file at the line the debug information gives, its own name
masked wherever it stands; the questions go a file at a time, every run's question of a batch
beginning with the same sources (a cached part), the proposals numbered as the functions; the
answers give a verdict for the name and one for the sentence, apart."""
import os

import pytest

from dread import llm, namejudge
from conftest import GIZMO

C_FILE = """/* A file of the test. */
#include "x.h"

static int
helper_twice( int x )
{
    return x * 2;   /* { not a brace } */
}

int helper_twice( int x );

void vTaskDelay( const int xTicks )
{
    traceENTER_vTaskDelay( xTicks );
    const char *s = "}{";
    if( xTicks > 0 )
    {
        helper_twice( xTicks );
    }
    traceRETURN_vTaskDelay();
}
"""


def test_a_definition_is_cut_from_its_line_to_its_closing_brace():
    """From the line the debug information names (a return type alone on the line before it too) to
    the brace that closes the body; braces in comments and strings do not count; a declaration has no
    body; the function's own name is masked inside longer identifiers too; a long one is cut."""
    text = namejudge.definition(C_FILE, 5, "helper_twice")
    assert text.startswith("static int\nhelper_twice( int x )\n{") and text.endswith("}")
    assert "return x * 2;" in text
    assert namejudge.definition(C_FILE, 10, "helper_twice") is None          # (a declaration)
    delay = namejudge.definition(C_FILE, 12, "vTaskDelay")
    assert delay.startswith("void vTaskDelay(") and delay.endswith("traceRETURN_vTaskDelay();\n}")
    assert namejudge.definition(C_FILE, 12, "vTaskSuspend") is None          # (another name there)
    shown = namejudge.masked(delay, "vTaskDelay")
    assert "void FUNCTION( const int xTicks )" in shown and "traceENTER_FUNCTION( xTicks );" in shown
    assert "vTaskDelay" not in shown and "helper_twice( xTicks )" in shown
    cut = namejudge.definition(C_FILE, 12, "vTaskDelay", max_chars=60)
    assert cut.endswith("more lines not shown */") and len(cut) < 120


def test_the_sources_come_from_the_debug_information_and_the_roots_given():
    """The gizmo build's debug information names its files relative to its compilation directory: found
    below the root given, a function's definition at its line; batches go a file at a time."""
    pytest.importorskip("elftools")
    elf = os.path.join(GIZMO, "m3", "original", "gizmo.elf")
    fw = os.path.join(GIZMO, "fw")
    sources = namejudge.source_functions(elf, roots=[fw])
    by_name = {s.name: s for s in sources.values()}
    assert {"uart_putc", "uart_puts", "gpio_set", "strlen"} <= set(by_name)
    putc = by_name["uart_putc"]
    assert putc.shown_path().endswith("hw.c") and os.path.isfile(putc.path) and "uart_putc" in putc.text.split("\n")[0]
    assert namejudge.find_file("hw.c", "/nowhere", [fw]) == os.path.normpath(os.path.join(fw, "hw.c"))
    assert namejudge.find_file("build/x/hw.c", "/nowhere", [fw]) == os.path.normpath(os.path.join(fw, "hw.c"))
    assert namejudge.find_file("nothing.c", "/nowhere", [fw]) is None
    entries = sorted(sources)
    batches = namejudge.batches(entries, sources, batch_functions=3)
    assert all(len(b) <= 3 and len({sources[e].path for e in b}) == 1 for _, b in batches)
    assert sorted(e for _, b in batches for e in b) == entries
    for path, batch in batches:
        part = namejudge.sources_part(path, batch, sources)
        assert "### Function 1" in part and "FUNCTION_1" in part
        assert not any(sources[e].name + "(" in part for e in batch), part     # (callers in the batch too)


class Counting(llm.Scripted):
    """A scripted provider that counts its answers as a real one does (without the ledger)."""

    def ask(self, system, messages, temperature=None):
        self.used["answers"] += 1
        return super().ask(system, messages, temperature)


def test_a_judge_sees_each_run_with_the_same_cached_sources():
    """Two runs judged together: each batch's questions begin with the same part, marked to be cached,
    then the proposals numbered as the functions (a function without a proposal left out); the verdicts
    of the name and of the sentence come apart; a question whose answer misses verdicts is asked again
    for those; a reason with a quote that breaks the JSON is read line by line."""
    sources = {0x10: namejudge.Source(0x10, "list_add", "/src/list.c", 1, "void list_add(void)\n{\n}"),
               0x20: namejudge.Source(0x20, "list_remove", "/src/list.c", 5, "void list_remove(void)\n{\n}"),
               0x30: namejudge.Source(0x30, "uart_init", "/src/uart.c", 1, "void uart_init(void)\n{\n}")}
    this = {0x10: ("list_insert", "Adds an item."), 0x20: ("list_unlink", "Removes an item."),
            0x30: ("serial_setup", "Sets the UART up."), 0x99: ("nothing", "No source.")}
    other = {0x10: ("list_append", "Appends."), 0x30: ("timer_start", "Starts a timer.")}
    asked = []

    def provider():
        answers = {("list.c", "list_insert"): ['```json\n{"1": "right | right"}\n```',      # (2 missing)
                                              '```json\n{"2": "partly: says unlink | wrong: it frees"}\n```'],
                   ("list.c", "list_append"): ['```json\n{"1": "right | partly: "says "appends""}\n```'],
                   ("uart.c", "serial_setup"): ['```json\n{"1": "right | right"}\n```'],
                   ("uart.c", "timer_start"): ['```json\n{"1": "wrong: it is the UART | wrong: no timer"}\n```']}

        def answer(messages):
            parts = messages[0]["content"]
            asked.append(parts)
            name = next(n for n in ("list_insert", "list_append", "serial_setup", "timer_start")
                        if n in parts[1]["text"])
            key = ("list.c" if "list.c" in parts[0]["text"] else "uart.c", name)
            return answers[key].pop(0)
        return Counting(answer)
    got = namejudge.judge(provider(), [("this", this), ("other", other)], sources)
    assert got["this"] == {0x10: ("right", "", "right", ""), 0x20: ("partly", "says unlink", "wrong", "it frees"),
                           0x30: ("right", "", "right", "")}
    assert got["other"][0x10][:3] == ("right", "", "partly") and got["other"][0x30][0] == "wrong"
    assert len(asked) == 5                                  # (list.c: this twice, other once; uart.c: both)
    firsts = [parts[0] for parts in asked]
    assert all(p["cache"] for p in firsts) and firsts[0] == firsts[1] == firsts[2]
    assert "list_add" not in firsts[0]["text"] and "### Function 2\n```c\nvoid FUNCTION_2(void)" in firsts[0]["text"]
    assert "1. list_insert: Adds an item.\n2. list_unlink: Removes an item." in asked[0][1]["text"]
    assert "2. " not in asked[2][1]["text"].split("```")[-1]       # (other has no proposal for list_remove)
    several = provider()                                    # (two batches at a time: the same verdicts, and the
    assert namejudge.judge(several, [("this", this), ("other", other)], sources, jobs=2) == got    # copies'
    assert several.used["answers"] == 5                     # answers counted as the provider's)
    one = llm.Scripted(['```json\n{"1": "wrong: no | right"}\n```', '```json\n{"1": "right"}\n```'])
    alone = namejudge.judge(one, [("x", {0x30: ("uart_init", "x")})], sources, sentence=False)
    assert alone["x"] == {0x30: ("right", "", None, "")}        # (a name-only answer: the first had two verdicts)
    assert not one.asked[0][1][0]["content"][0]["cache"]       # (one run alone: nothing to share)
    assert "2." not in namejudge.proposals_part([0x30], {0x30: ("a", "b")}, sentence=False).split("```")[-1]


def test_a_run_judged_once_is_answered_from_the_kept_answers(tmp_path):
    """judge's cache: the answers asked are kept by question, a question asked again because its answer
    missed verdicts under its own attempt; judged again with the same cache, nothing is asked and the
    verdicts are the same, the attempts replayed in their order; another run's proposals are asked."""
    sources = {0x10: namejudge.Source(0x10, "list_add", "/src/list.c", 1, "void list_add(void)\n{\n}"),
               0x20: namejudge.Source(0x20, "list_remove", "/src/list.c", 5, "void list_remove(void)\n{\n}")}
    runs = [("x", {0x10: ("list_insert", "Adds."), 0x20: ("list_unlink", "Removes.")})]
    first = Counting(['```json\n{"1": "right | right"}\n```', '```json\n{"2": "wrong: frees | right"}\n```'])
    path = str(tmp_path / "answers.jsonl")
    got = namejudge.judge(first, runs, sources, cache=path)
    assert got["x"] == {0x10: ("right", "", "right", ""), 0x20: ("wrong", "frees", "right", "")}
    assert first.used["answers"] == 2
    kept = llm.AnswerCache(path)
    again = Counting([])
    assert namejudge.judge(again, runs, sources, cache=kept, jobs=2) == got
    assert again.used["answers"] == 0 and kept.hits == 2
    other = Counting(['```json\n{"1": "partly: vague | right", "2": "right | right"}\n```'])
    changed = [("x", {0x10: ("list_op", "Does it."), 0x20: ("list_unlink", "Removes.")})]
    assert namejudge.judge(other, changed, sources, cache=kept)["x"][0x10][0] == "partly"
    assert other.used["answers"] == 1


def test_a_judge_asks_in_batches_with_the_first_run_writing_the_cache(tmp_path):
    """judge with batch: the same verdicts as asked one at a time; every source batch's question of the
    first run goes first, its sources marked for the prompt cache for an hour, the other runs' questions
    of that batch once it has ended (a question missing verdicts asked again in the next round), each
    round a Message Batch; the answers are kept by question as before and a judging again asks nothing."""
    sources = {0x10: namejudge.Source(0x10, "list_add", "/src/list.c", 1, "void list_add(void)\n{\n}"),
               0x30: namejudge.Source(0x30, "uart_init", "/src/uart.c", 1, "void uart_init(void)\n{\n}"),
               0x40: namejudge.Source(0x40, "uart_send", "/src/uart.c", 5, "void uart_send(void)\n{\n}")}
    this = {0x10: ("list_insert", "Adds."), 0x30: ("serial_setup", "Sets it up."), 0x40: ("serial_put", "Sends.")}
    other = {0x10: ("list_append", "Appends."), 0x30: ("timer_start", "Starts a timer.")}

    def answering():                        # (a run's answers: the question about serial_setup misses a
        times = {}                          # verdict the first time it is asked, and is asked again)

        def answer(messages):
            text = messages[0]["content"][1]["text"]
            times[text] = times.get(text, 0) + 1
            if "list_insert" in text:
                return '```json\n{"1": "right | right"}\n```'
            if "list_append" in text:
                return '```json\n{"1": "partly: vague | right"}\n```'
            if "serial_setup" in text and times[text] == 1:
                return '```json\n{"1": "right | right"}\n```'
            if "serial_setup" in text:
                return '```json\n{"2": "right | partly: sends a byte"}\n```'
            return '```json\n{"1": "wrong: no timer | wrong: no"}\n```'
        return answer
    plain = namejudge.judge(llm.Scripted(answering()), [("this", this), ("other", other)], sources)
    batching = llm.Scripted(answering(), batches=True)
    path = str(tmp_path / "answers.jsonl")
    got = namejudge.judge(batching, [("this", this), ("other", other)], sources, batch=1, cache=path)
    assert got == plain and got["this"][0x40] == ("right", "", "partly", "sends a byte"), got
    rounds = [[llm.content_text(p["messages"][0]["content"][1]["text"]).split("\n")[-1] for _, p in b["requests"]]
              for b in batching.submitted]
    assert [len(r) for r in rounds] == [2, 2, 1], rounds    # (this: list.c, uart.c; uart.c again, list.c's other;
    first = batching.submitted[0]["requests"][0][1]["messages"][0]["content"][0]    # uart.c's other)
    assert first["cache"] == "1h" and batching.used["answers"] == 5 and batching.used["batch"] == 5
    again = llm.Scripted([], batches=True)
    assert namejudge.judge(again, [("this", this), ("other", other)], sources, batch=1, cache=path) == got
    assert again.submitted == []

// DREAD: set up a freshly imported image before Ghidra's analysis, from a directive file written by
// `dread ghidra` (all facts about the target come from there, none from this script):
//   image BASE END                    the imported block: executes in place, read-only
//   copy NAME RUN STORED LEN EXEC     bytes the startup copies (EXEC 1: code)
//   ram NAME BASE SIZE / rom NAME BASE SIZE / io NAME BASE SIZE   uninitialised blocks (io: volatile)
//   data START END                    a range that is data: not executable
//   thumb START END / arm START END   the instruction set of a range (the TMode context register)
//   func ADDR NAME|-                  a function start, with its name or without
//   label ADDR NAME                   a data name
//   entry ADDR                        the reset entry
//   option NAME=VALUE                 an analysis option (NAME as Ghidra's analysis options show it)
// Script args: <directive file>
//@category DREAD

import java.io.ByteArrayInputStream;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Paths;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;

public class DreadSetup extends GhidraScript {

	private Address addr(long offset) {
		return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(offset);
	}

	private void copy(String name, long run, long stored, int len, boolean exec) throws Exception {
		Memory mem = currentProgram.getMemory();
		byte[] bytes = new byte[len];
		mem.getBytes(addr(stored), bytes);
		MemoryBlock block = mem.getBlock(addr(run));
		if (block == null) {
			block = mem.createInitializedBlock(name, addr(run), new ByteArrayInputStream(bytes), len,
				monitor, false);
		}
		else {
			if (block.getStart().getOffset() < run) {
				mem.split(block, addr(run));
				block = mem.getBlock(addr(run));
			}
			if (block.getEnd().getOffset() >= run + len) {
				mem.split(block, addr(run + len));
				block = mem.getBlock(addr(run));
			}
			if (!block.isInitialized()) {
				block = mem.convertToInitialized(block, (byte) 0);
			}
			mem.setBytes(addr(run), bytes);
			block.setName(name);
		}
		block.setRead(true);
		// Code copied to RAM is not changed afterwards; read-only lets the decompiler fold its
		// literal pools into constants.
		block.setWrite(!exec);
		block.setExecute(exec);
		println(String.format("%s: %#x <- %#x (%#x bytes)", name, run, stored, len));
	}

	private void uninit(String name, long base, long size, boolean write, boolean vol) throws Exception {
		if (size <= 0 || currentProgram.getMemory().getBlock(addr(base)) != null) {
			return;
		}
		MemoryBlock block = currentProgram.getMemory().createUninitializedBlock(name, addr(base), size, false);
		block.setRead(true);
		block.setWrite(write);
		block.setExecute(false);
		block.setVolatile(vol);
	}

	private void notExecutable(long start, long end) throws Exception {
		Memory mem = currentProgram.getMemory();
		Address s = addr(start), e = addr(end);
		MemoryBlock block = mem.getBlock(s);
		if (block == null) {
			return;
		}
		if (block.getStart().compareTo(s) < 0) {
			mem.split(block, s);
		}
		MemoryBlock inner = mem.getBlock(s);
		if (mem.getBlock(e) != null && mem.getBlock(e) == inner && inner.getStart().compareTo(e) < 0) {
			mem.split(inner, e);
		}
		mem.getBlock(s).setExecute(false);
	}

	private void isa(long start, long end, int value) throws Exception {
		Register tmode = currentProgram.getProgramContext().getRegister("TMode");
		if (tmode != null) {
			currentProgram.getProgramContext().setValue(tmode, addr(start), addr(end - 1), BigInteger.valueOf(value));
		}
	}

	private void function(long a, String name) throws Exception {
		MemoryBlock block = currentProgram.getMemory().getBlock(addr(a));
		if (block == null || !block.isExecute() || !block.isInitialized()) {
			println(String.format("function start %#x is not in executable memory: ignored", a));
			return;
		}
		disassemble(addr(a));
		if (getFunctionAt(addr(a)) == null) {
			createFunction(addr(a), null);
		}
		if (name != null && getFunctionAt(addr(a)) != null) {
			getFunctionAt(addr(a)).setName(name, SourceType.USER_DEFINED);
		}
	}

	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 1) {
			throw new IllegalArgumentException("usage: DreadSetup.java <directive file>");
		}
		int functions = 0, labels = 0;
		for (String line : Files.readAllLines(Paths.get(args[0]))) {
			String[] f = line.trim().split("\\s+");
			if (f.length == 0 || f[0].isEmpty() || f[0].startsWith("#")) {
				continue;
			}
			if (f[0].equals("option")) {
				String rest = line.trim().substring("option".length()).trim();
				int eq = rest.indexOf('=');
				setAnalysisOption(currentProgram, rest.substring(0, eq).trim(), rest.substring(eq + 1).trim());
				println("analysis option " + rest);
				continue;
			}
			switch (f[0]) {
				case "image": {
					MemoryBlock b = currentProgram.getMemory().getBlock(addr(Long.decode(f[1])));
					b.setWrite(false);
					b.setExecute(true);
					break;
				}
				case "copy":
					copy(f[1], Long.decode(f[2]), Long.decode(f[3]), Integer.decode(f[4]), f[5].equals("1"));
					break;
				case "ram":
					uninit(f[1], Long.decode(f[2]), Long.decode(f[3]), true, false);
					break;
				case "rom":
					uninit(f[1], Long.decode(f[2]), Long.decode(f[3]), false, false);
					break;
				case "io":
					uninit(f[1], Long.decode(f[2]), Long.decode(f[3]), true, true);
					break;
				case "data":
					notExecutable(Long.decode(f[1]), Long.decode(f[2]));
					break;
				case "thumb":
					isa(Long.decode(f[1]), Long.decode(f[2]), 1);
					break;
				case "arm":
					isa(Long.decode(f[1]), Long.decode(f[2]), 0);
					break;
				case "func":
					function(Long.decode(f[1]), f.length > 2 && !f[2].equals("-") ? f[2] : null);
					functions++;
					break;
				case "label":
					createLabel(addr(Long.decode(f[1])), f[2], true, SourceType.USER_DEFINED);
					labels++;
					break;
				case "entry":
					function(Long.decode(f[1]), null);
					addEntryPoint(addr(Long.decode(f[1])));
					break;
				default:
					throw new IllegalArgumentException("unknown directive: " + line);
			}
		}
		println("function starts: " + functions + ", data labels: " + labels);
	}
}

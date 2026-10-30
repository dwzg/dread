// DREAD: export the analysis as DREAD's tables (docs/formats.md):
//   functions.tsv    address, name, instruction bytes, prototype (every function Ghidra knows)
//   code_ranges.tsv  runs of instructions, end exclusive, with their instruction set: the third
//                    argument's (default a32), or t32 where ARM's TMode register says Thumb
// Script args: <functions.tsv> <code_ranges.tsv>
//@category DREAD

import java.io.PrintWriter;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.HashSet;
import java.util.Set;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;

public class DreadExportMap extends GhidraScript {

	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 2) {
			throw new IllegalArgumentException("usage: DreadExportMap.java <functions.tsv> <code_ranges.tsv>");
		}
		int functions = 0;
		Set<String> taken = new HashSet<>();
		try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(Paths.get(args[0]), StandardCharsets.UTF_8))) {
			out.println("address\tname\tbytes\tprototype");
			for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
				if (f.isExternal()) {
					continue;
				}
				ghidra.program.model.mem.MemoryBlock block = currentProgram.getMemory().getBlock(f.getEntryPoint());
				if (block == null || !block.isInitialized()) {
					// a call's target in memory the image does not fill (code another loader puts there
					// at run time): Ghidra makes a function of no instructions there, which is not the image's
					continue;
				}
				String name = f.getName();
				if (f.isThunk()) {
					// a thunk shows its target's name; the table needs names of its own
					Function t = f.getThunkedFunction(false);
					if (t != null && name.equals(t.getName())) {
						name = "thunk_" + name;
					}
				}
				if (!taken.add(name)) {
					// two functions of one name (two thunks of one function): the second takes its address
					name = String.format("%s_%x", name, f.getEntryPoint().getOffset());
					taken.add(name);
				}
				out.printf("0x%x\t%s\t%d\t%s%n", f.getEntryPoint().getOffset(), name,
					f.getBody().getNumAddresses(), f.getPrototypeString(false, false));
				functions++;
			}
		}
		Register tmode = currentProgram.getProgramContext().getRegister("TMode");
		String plain = args.length > 2 ? args[2] : "a32";
		long start = -1, end = -1;
		String runIsa = null;
		int ranges = 0;
		try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(Paths.get(args[1]), StandardCharsets.UTF_8))) {
			out.println("# start\tend (exclusive)\tisa: runs of instructions as Ghidra classified them");
			for (Instruction ins : currentProgram.getListing().getInstructions(true)) {
				long a = ins.getMinAddress().getOffset();
				String isa = plain;
				if (tmode != null) {
					BigInteger v = ins.getValue(tmode, false);
					if (v != null && v.intValue() == 1) {
						isa = "t32";
					}
				}
				if (a != end || !isa.equals(runIsa)) {
					if (start >= 0) {
						out.printf("0x%x\t0x%x\t%s%n", start, end, runIsa);
						ranges++;
					}
					start = a;
					runIsa = isa;
				}
				end = a + ins.getLength();
			}
			if (start >= 0) {
				out.printf("0x%x\t0x%x\t%s%n", start, end, runIsa);
				ranges++;
			}
		}
		println("exported " + functions + " functions and " + ranges + " code ranges");
	}
}

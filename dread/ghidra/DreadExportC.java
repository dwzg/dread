// DREAD: decompile every function into <outdir>/<region>.c, one file per region of the address
// space (reference C for reading, not for compiling).
// Script args: <outdir> <title> <region size>
//@category DREAD

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

public class DreadExportC extends GhidraScript {

	private static final int TIMEOUT_SECONDS = 120;

	@Override
	public void run() throws Exception {
		String[] args = getScriptArgs();
		if (args.length < 3) {
			throw new IllegalArgumentException("usage: DreadExportC.java <outdir> <title> <region size>");
		}
		Path outDir = Paths.get(args[0]);
		String title = args[1];
		long region = Long.decode(args[2]);
		Files.createDirectories(outDir);
		DecompInterface decomp = new DecompInterface();
		decomp.setOptions(new DecompileOptions());
		decomp.toggleCCode(true);
		decomp.toggleSyntaxTree(false);
		if (!decomp.openProgram(currentProgram)) {
			throw new RuntimeException("decompiler failed to start: " + decomp.getLastMessage());
		}
		Map<String, PrintWriter> files = new LinkedHashMap<>();
		int ok = 0, failed = 0;
		try {
			for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
				if (monitor.isCancelled()) {
					break;
				}
				if (f.isExternal()) {
					continue;
				}
				long entry = f.getEntryPoint().getOffset();
				long start = entry - Long.remainderUnsigned(entry, region);
				String file = String.format("%08x.c", start);
				PrintWriter out = files.get(file);
				if (out == null) {
					out = new PrintWriter(Files.newBufferedWriter(outDir.resolve(file), StandardCharsets.UTF_8));
					out.printf("// %s, Ghidra's decompilation of region %08x (for reading; not compilable)%n%n",
						title, start);
					files.put(file, out);
				}
				DecompileResults res = decomp.decompileFunction(f, TIMEOUT_SECONDS, monitor);
				out.printf("// ---- %s @ 0x%x (%d bytes) ----%n", f.getName(), entry, f.getBody().getNumAddresses());
				if (res != null && res.decompileCompleted() && res.getDecompiledFunction() != null) {
					out.println(res.getDecompiledFunction().getC().trim());
					ok++;
				}
				else {
					String msg = res == null ? "no result" : res.getErrorMessage();
					out.printf("/* decompilation failed: %s */%n", msg == null ? "" : msg.trim());
					failed++;
				}
				out.println();
			}
		}
		finally {
			for (PrintWriter w : files.values()) {
				w.close();
			}
			decomp.dispose();
		}
		println("decompiled " + ok + " functions, " + failed + " failed, into " + outDir);
	}
}

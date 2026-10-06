package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeFalse;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Host duty H1: which program starts, and that a project can never choose it. */
class ProgramLocatorTest {
    private static final String EXE = ProgramLocator.windows() ? ".exe" : "";

    private static Path program(Path dir, String name) throws IOException {
        Files.createDirectories(dir);
        Path p = dir.resolve(name);
        Files.writeString(p, "#!/bin/sh\necho hi\n");
        p.toFile().setExecutable(true);
        return p;
    }

    @Test
    void aProgramPlantedInTheProjectIsNeverStarted(@TempDir Path tmp) throws IOException {
        Path project = tmp.resolve("project");
        program(project, "cmcoder" + EXE);
        Path tools = tmp.resolve("tools");
        Path real = program(tools, "cmcoder" + EXE);
        // The project folder first on PATH, a relative entry too: both skipped.
        String path = project + File.pathSeparator + "." + File.pathSeparator + tools;
        assertEquals(real, ProgramLocator.onPath("cmcoder", project, Map.of("PATH", path, "PATHEXT", ".EXE")));
        assertNull(ProgramLocator.onPath("cmcoder", project, Map.of("PATH", project.toString())));
        assertNull(ProgramLocator.onPath("cmcoder", project, Map.of("PATH", "relative" + File.separator + "dir")));
    }

    @Test
    void theSettingThenTheBundledCopyThenPath(@TempDir Path tmp) throws IOException {
        Path project = Files.createDirectories(tmp.resolve("project"));
        Path plugin = tmp.resolve("plugin");
        Path bundled = program(plugin.resolve("bin").resolve("cmcoder"), "cmcoder" + EXE);
        Path onPath = program(tmp.resolve("tools"), "cmcoder" + EXE);
        Path mine = program(tmp.resolve("mine"), "cmcoder" + EXE);
        Map<String, String> env = Map.of("PATH", onPath.getParent().toString());

        assertEquals(mine, ProgramLocator.find(mine.toString(), plugin, project, env).program);
        assertEquals(bundled, ProgramLocator.find(null, plugin, project, env).program);
        assertEquals(bundled, ProgramLocator.find("  ", plugin, project, env).program);
        assertEquals(onPath, ProgramLocator.find(null, tmp.resolve("no-plugin"), project, env).program);

        ProgramLocator.Found none = ProgramLocator.find(null, null, project, Map.of("PATH", ""));
        assertNull(none.program);
        assertTrue(none.problem.contains("plugin file for your platform"), none.problem);
    }

    @Test
    void theSettingMustBeAFullPathToAProgram(@TempDir Path tmp) throws IOException {
        Path project = Files.createDirectories(tmp.resolve("project"));
        ProgramLocator.Found relative = ProgramLocator.find("tools/cmcoder", null, project, Map.of());
        assertNull(relative.program);
        assertTrue(relative.problem.contains("full path"));
        ProgramLocator.Found missing = ProgramLocator.find(tmp.resolve("nope").toString(), null, project, Map.of());
        assertTrue(missing.problem.contains("doesn't exist"));
        Path batch = program(tmp, "cmcoder.cmd");
        assertTrue(ProgramLocator.find(batch.toString(), null, project, Map.of()).problem.contains("batch file"));
    }

    @Test
    void batchFilesOnPathAreSkipped(@TempDir Path tmp) throws IOException {
        Path tools = tmp.resolve("tools");
        program(tools, "cmcoder.cmd");
        program(tools, "cmcoder.bat");
        assertTrue(ProgramLocator.batch("x.CMD") && ProgramLocator.batch("x.bat"));
        if (ProgramLocator.windows()) {
            assertNull(ProgramLocator.onPath("cmcoder", tmp, Map.of("PATH", tools.toString(), "PATHEXT", ".BAT;.CMD;.EXE")));
        }
    }

    @Test
    void prepareRestoresTheExecutableBit(@TempDir Path tmp) throws IOException {
        assumeFalse(ProgramLocator.windows(), "no executable bit on Windows");
        Path p = program(tmp.resolve("bin").resolve("cmcoder"), "cmcoder");
        p.toFile().setExecutable(false, false);
        assertTrue(!Files.isExecutable(p));
        ProgramLocator.prepare(p);
        assertTrue(Files.isExecutable(p));
        ProgramLocator.prepare(tmp.resolve("missing")); // nothing to do, no error
    }
}

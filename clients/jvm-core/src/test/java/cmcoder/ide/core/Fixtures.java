package cmcoder.ide.core;

import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * What the tests run against:
 *
 * <ul>
 *   <li>the cmcoder program: {@code CMCODER_TEST_BINARY} (the standalone build,
 *       as developers get it; the release workflow sets it), else cmcoder from
 *       source through a small wrapper around {@code CMCODER_TEST_PYTHON}
 *       (macOS and Linux only: on Windows the wrapper would be a batch file,
 *       which the core refuses to start);
 *   <li>the mock model server, from {@code CMCODER_TEST_PYTHON} (test
 *       equipment, not part of what developers get).
 * </ul>
 */
final class Fixtures {
    static final String API_KEY = "sk-test-key";
    private static Path program;

    private Fixtures() {}

    static String python() {
        String p = System.getenv("CMCODER_TEST_PYTHON");
        assumeTrue(p != null && !p.isEmpty(), "set CMCODER_TEST_PYTHON (a Python with cmcoder) for the mock server");
        return p;
    }

    static synchronized Path program() throws IOException {
        if (program != null) return program;
        String binary = System.getenv("CMCODER_TEST_BINARY");
        if (binary != null && !binary.isEmpty()) {
            program = Paths.get(binary).toAbsolutePath();
            return program;
        }
        assumeTrue(!ProgramLocator.windows(), "on Windows set CMCODER_TEST_BINARY (the standalone cmcoder.exe)");
        Path dir = Files.createTempDirectory("cmcoder-wrapper-");
        Path wrapper = dir.resolve("cmcoder");
        Files.writeString(wrapper, "#!/bin/sh\nexec '" + python().replace("'", "'\\''") + "' -m cmcoder \"$@\"\n");
        if (!wrapper.toFile().setExecutable(true)) throw new IOException("can't make " + wrapper + " executable");
        program = wrapper;
        return program;
    }

    static Path project() throws IOException {
        Path dir = Files.createTempDirectory("cmcoder-proj-");
        Files.createDirectory(dir.resolve(".git"));
        return dir;
    }

    /** The environment for cmcoder: the mock server, a private settings folder, no proxy. */
    static Map<String, String> env(String baseUrl) throws IOException {
        Map<String, String> env = new HashMap<>();
        env.put("CMCODER_BASE_URL", baseUrl);
        env.put("CMCODER_API_KEY", API_KEY);
        env.put("CMCODER_MODEL", "qwen3-27b");
        env.put("CMCODER_CONFIG_DIR", Files.createTempDirectory("cmcoder-config-").toString());
        env.put("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring");
        for (String proxy : new String[] {"HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"}) {
            env.put(proxy, null);
        }
        return env;
    }

    /** The mock model server with a script of replies (JSON array). */
    static final class MockServer implements AutoCloseable {
        final Process process;
        final String url;
        final Path record;

        MockServer(String scriptJson) throws IOException {
            Path dir = Files.createTempDirectory("cmcoder-mock-");
            Path script = dir.resolve("script.json");
            Files.writeString(script, scriptJson);
            record = dir.resolve("requests.jsonl");
            List<String> command = new ArrayList<>(List.of(python(), "-m", "cmcoder.testing.mock_server",
                    "--script", script.toString(), "--port", "0", "--api-key", API_KEY, "--record", record.toString()));
            process = new ProcessBuilder(command).redirectError(ProcessBuilder.Redirect.INHERIT).start();
            BufferedReader out = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
            String line = out.readLine();
            if (line == null || !line.startsWith("mock server on ")) throw new IOException("mock server didn't start: " + line);
            url = line.substring("mock server on ".length()).trim();
        }

        /** The chat requests the model got (JSON objects). */
        List<Map<String, Object>> requests() throws IOException {
            List<Map<String, Object>> out = new ArrayList<>();
            if (!Files.exists(record)) return out;
            for (String line : Files.readAllLines(record, StandardCharsets.UTF_8)) out.add(Json.parseObject(line));
            return out;
        }

        @Override
        public void close() {
            process.destroy();
            try {
                process.waitFor(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }
}

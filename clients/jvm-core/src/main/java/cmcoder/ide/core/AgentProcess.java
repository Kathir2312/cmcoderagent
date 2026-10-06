package cmcoder.ide.core;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * One {@code cmcoder --protocol stdio} process: one conversation (host duties
 * H2-H4). The same behaviour as the VS Code extension's agentProcess.ts:
 *
 * <ul>
 *   <li>started without a shell (arguments are never re-parsed) and, on
 *       Windows, without a console window (Java starts console programs from
 *       an IDE with CREATE_NO_WINDOW);
 *   <li>stdout read as UTF-8 lines, each a protocol event; anything else, and
 *       stderr, goes to the log;
 *   <li>stopped with {@code shutdown}, then end of input, then (after a time
 *       limit) by force, including anything it started, so closing a project
 *       leaves nothing running.
 * </ul>
 *
 * Listener calls come from reader threads; an IDE moves them to its UI thread.
 */
public final class AgentProcess {
    public interface Listener {
        void onEvent(Protocol.Event event);

        /** stderr, and stdout lines that aren't events. */
        void onLog(String line);

        /**
         * The process ended. {@code expected}: it was asked to stop. {@code error}:
         * why it couldn't start (null otherwise).
         */
        void onExit(Integer code, boolean expected, String error);
    }

    private final Process process;
    private final OutputStream stdin;
    private final Listener listener;
    private final AtomicBoolean stopping = new AtomicBoolean();
    private final AtomicBoolean exited = new AtomicBoolean();
    private final CountDownLatch done = new CountDownLatch(1);
    private final Object writeLock = new Object();

    private AgentProcess(Process process, Listener listener) {
        this.process = process;
        this.stdin = process == null ? null : process.getOutputStream();
        this.listener = listener;
    }

    /**
     * Starts cmcoder. A start that fails is reported through {@code onExit}
     * (never thrown), like a crash.
     *
     * @param program   the program (from {@link ProgramLocator})
     * @param extraArgs cmcoder's arguments after {@code --protocol stdio}
     * @param env       variables to add or change (a null value removes one)
     */
    public static AgentProcess start(Path program, List<String> extraArgs, Path cwd, Map<String, String> env, Listener listener) {
        List<String> command = new ArrayList<>();
        command.add(program.toString());
        command.add("--protocol");
        command.add("stdio");
        command.addAll(extraArgs);
        ProcessBuilder pb = new ProcessBuilder(command).directory(cwd.toFile());
        Map<String, String> environment = pb.environment();
        for (Map.Entry<String, String> e : env.entrySet()) {
            if (e.getValue() == null) environment.remove(e.getKey());
            else environment.put(e.getKey(), e.getValue());
        }
        environment.put("NO_COLOR", "1");
        Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            AgentProcess failed = new AgentProcess(null, listener);
            failed.finish(null, describeStartError(program, e));
            return failed;
        }
        AgentProcess agent = new AgentProcess(p, listener);
        Thread out = agent.read(p.getInputStream(), true);
        Thread err = agent.read(p.getErrorStream(), false);
        Thread waiter = new Thread(() -> {
            try {
                int code = p.waitFor();
                // Every line it wrote is handled before its end is reported.
                out.join(5000);
                err.join(2000);
                agent.finish(code, null);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }, "cmcoder-exit");
        waiter.setDaemon(true);
        waiter.start();
        return agent;
    }

    /** The process (tests, and an IDE that shows its id in the log). */
    ProcessHandle handle() {
        return process == null ? null : process.toHandle();
    }

    public boolean running() {
        return !exited.get();
    }

    /** Sends one message (a JSON object); false if cmcoder isn't running. */
    public boolean send(String json) {
        if (exited.get() || stdin == null) return false;
        byte[] line = (json + "\n").getBytes(StandardCharsets.UTF_8);
        synchronized (writeLock) {
            try {
                stdin.write(line);
                stdin.flush();
                return true;
            } catch (IOException e) {
                return false; // it went away; onExit reports it
            }
        }
    }

    /** Asks cmcoder to finish (it saves the session), then makes sure it's gone. */
    public void stop(long timeoutMillis) {
        if (exited.get()) return;
        stopping.set(true);
        send(Protocol.shutdown());
        synchronized (writeLock) {
            try {
                stdin.close();
            } catch (IOException ignored) {
                // already closed
            }
        }
        try {
            if (!done.await(timeoutMillis, TimeUnit.MILLISECONDS)) {
                process.descendants().forEach(ProcessHandle::destroy);
                process.destroy();
                if (!done.await(2, TimeUnit.SECONDS)) {
                    process.descendants().forEach(ProcessHandle::destroyForcibly);
                    process.destroyForcibly();
                    done.await(5, TimeUnit.SECONDS);
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            process.destroyForcibly();
        }
    }

    public void stop() {
        stop(5000);
    }

    /** Waits for the end (tests). */
    public boolean awaitExit(long timeoutMillis) throws InterruptedException {
        return done.await(timeoutMillis, TimeUnit.MILLISECONDS);
    }

    private Thread read(InputStream stream, boolean stdout) {
        Thread t = new Thread(() -> {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) {
                    if (!stdout) {
                        listener.onLog(line);
                        continue;
                    }
                    if (line.trim().isEmpty()) continue;
                    Protocol.Event event = Protocol.Event.parse(line);
                    if (event == null) listener.onLog("[stdout] " + line);
                    else listener.onEvent(event);
                }
            } catch (IOException e) {
                // the stream closed with the process
            }
        }, stdout ? "cmcoder-stdout" : "cmcoder-stderr");
        t.setDaemon(true);
        t.start();
        return t;
    }

    private void finish(Integer code, String error) {
        if (!exited.compareAndSet(false, true)) return;
        listener.onExit(code, stopping.get() && error == null, error);
        done.countDown();
    }

    static String describeStartError(Path program, IOException e) {
        String message = String.valueOf(e.getMessage());
        if (message.contains("error=2") || message.contains("No such file") || message.contains("cannot find the file")) {
            return "Couldn't start \"" + program + "\": not found. Install the plugin file for your platform "
                    + "(it includes cmcoder), or set the cmcoder program's full path in the settings.";
        }
        if (message.contains("error=13") || message.contains("Permission denied")) {
            return "Couldn't start \"" + program + "\": it isn't allowed to run (permission denied). "
                    + "Reinstall the plugin, or check that your company's security software allows it.";
        }
        return "Couldn't start \"" + program + "\": " + message;
    }
}
